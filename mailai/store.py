"""The local mailbox: a SQLite file you can point any tool at.

Threads are derived from messages by query rather than kept as their own table. A thread is
not an independent fact -- it is whatever messages share a threadId -- and storing it
separately means two things that can disagree, with no way to tell which is right.

sqlite3 is in the standard library, so a mailbox costs no dependency and no service.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id                  TEXT PRIMARY KEY,
    thread_id           TEXT NOT NULL,
    internal_date       INTEGER NOT NULL,
    from_addr           TEXT,
    to_addr             TEXT,
    cc_addr             TEXT,
    subject             TEXT,
    normalised_subject  TEXT,
    snippet             TEXT,
    body                TEXT,
    quoted              TEXT,
    labels              TEXT,
    attachments         TEXT,
    rfc822_message_id   TEXT
);
CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread_id, internal_date);
CREATE INDEX IF NOT EXISTS messages_date   ON messages(internal_date DESC);

CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


@dataclass(frozen=True)
class StoredMessage:
    id: str
    thread_id: str
    internal_date: int
    from_addr: str
    to_addr: str
    subject: str
    snippet: str
    body: str
    labels: list[str]


# Columns added after the first release. A mailbox is a local cache that can always be
# rebuilt, so migrating is adding the column and letting the next sync fill it -- there is
# no data to preserve and no version table to maintain.
ADDED_COLUMNS = {
    # The ORIGINAL message's RFC 822 Message-ID, which is not Gmail's own message id.
    # Replying without it threads correctly in Gmail's UI and nowhere else.
    "rfc822_message_id": "TEXT",
}


def open_store(path: Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    _migrate(connection)
    return connection


def _migrate(connection: sqlite3.Connection) -> None:
    existing = {row["name"] for row in connection.execute("PRAGMA table_info(messages)")}
    for column, kind in ADDED_COLUMNS.items():
        if column not in existing:
            connection.execute(f"ALTER TABLE messages ADD COLUMN {column} {kind}")
    connection.commit()


def upsert_message(connection: sqlite3.Connection, message: dict[str, Any]) -> None:
    """Insert or replace one message. Idempotent: a re-sync must not duplicate anything."""
    connection.execute(
        """
        INSERT INTO messages (id, thread_id, internal_date, from_addr, to_addr, cc_addr,
                              subject, normalised_subject, snippet, body, quoted, labels,
                              attachments, rfc822_message_id)
        VALUES (:id, :thread_id, :internal_date, :from_addr, :to_addr, :cc_addr, :subject,
                :normalised_subject, :snippet, :body, :quoted, :labels, :attachments,
                :rfc822_message_id)
        ON CONFLICT(id) DO UPDATE SET
            thread_id=excluded.thread_id, internal_date=excluded.internal_date,
            from_addr=excluded.from_addr, to_addr=excluded.to_addr, cc_addr=excluded.cc_addr,
            subject=excluded.subject, normalised_subject=excluded.normalised_subject,
            snippet=excluded.snippet, body=excluded.body, quoted=excluded.quoted,
            labels=excluded.labels, attachments=excluded.attachments,
            rfc822_message_id=excluded.rfc822_message_id
        """,
        {
            "rfc822_message_id": "",
            **message,
            "labels": json.dumps(message.get("labels", [])),
            "attachments": json.dumps(message.get("attachments", [])),
        },
    )


def delete_message(connection: sqlite3.Connection, message_id: str) -> None:
    connection.execute("DELETE FROM messages WHERE id = ?", (message_id,))


def set_labels(connection: sqlite3.Connection, message_id: str, labels: Iterable[str]) -> None:
    connection.execute(
        "UPDATE messages SET labels = ? WHERE id = ?", (json.dumps(sorted(set(labels))), message_id)
    )


def labels_of(connection: sqlite3.Connection, message_id: str) -> list[str]:
    row = connection.execute("SELECT labels FROM messages WHERE id = ?", (message_id,)).fetchone()
    return json.loads(row["labels"]) if row and row["labels"] else []


def has_message(connection: sqlite3.Connection, message_id: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM messages WHERE id = ?", (message_id,)
    ).fetchone() is not None


def get_state(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_state(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO sync_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def message_count(connection: sqlite3.Connection) -> int:
    return int(connection.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"])


def threads(connection: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """Newest-first thread list, derived rather than stored.

    The subject shown is the earliest message's normalised one: a thread is named by how it
    started, not by whatever the last person typed after the Re:.
    """
    rows = connection.execute(
        """
        SELECT thread_id,
               COUNT(*)              AS messages,
               MAX(internal_date)    AS last_date,
               MIN(internal_date)    AS first_date
        FROM messages
        GROUP BY thread_id
        ORDER BY last_date DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        first = connection.execute(
            "SELECT normalised_subject, from_addr FROM messages "
            "WHERE thread_id = ? ORDER BY internal_date ASC LIMIT 1",
            (row["thread_id"],),
        ).fetchone()
        out.append({
            "thread_id": row["thread_id"],
            "subject": first["normalised_subject"] if first else "",
            "started_by": first["from_addr"] if first else "",
            "messages": row["messages"],
            "last_date": row["last_date"],
        })
    return out


def thread_messages(connection: sqlite3.Connection, thread_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT id, from_addr, to_addr, subject, internal_date, body, labels, "
        "rfc822_message_id FROM messages WHERE thread_id = ? ORDER BY internal_date ASC",
        (thread_id,),
    ).fetchall()
    return [{**dict(row), "labels": json.loads(row["labels"] or "[]")} for row in rows]


def search(connection: sqlite3.Connection, text: str, limit: int = 20) -> list[dict[str, Any]]:
    like = f"%{text}%"
    rows = connection.execute(
        "SELECT id, thread_id, from_addr, subject, internal_date, snippet FROM messages "
        "WHERE subject LIKE ? OR body LIKE ? OR from_addr LIKE ? "
        "ORDER BY internal_date DESC LIMIT ?",
        (like, like, like, limit),
    ).fetchall()
    return [dict(row) for row in rows]
