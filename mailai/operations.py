"""What mail-ai can be asked to do, as data in and data out.

Both front doors sit on this. Nothing here writes to stdout or sets an exit code -- that is
a front door's job -- and nothing here makes a judgment about an email. Triage, drafting
and summarising are a later milestone and land in one declared seam above this.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mailai import store as db
from mailai.auth import AuthConfig, load_credentials
from mailai.sync import FULL_SYNC_DONE, HISTORY_KEY, Progress, sync


def _when(ms: int) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="minutes")


@dataclass(frozen=True)
class MailboxStatus:
    path: str
    messages: int
    threads: int
    watermark: str | None
    full_sync_complete: bool
    newest: str


def status(store_path: Path) -> MailboxStatus:
    """Read-only, offline, and needs no credentials. The safe thing to run first."""
    connection = db.open_store(store_path)
    rows = connection.execute(
        "SELECT COUNT(DISTINCT thread_id) AS t, MAX(internal_date) AS newest FROM messages"
    ).fetchone()
    return MailboxStatus(
        path=str(store_path),
        messages=db.message_count(connection),
        threads=int(rows["t"] or 0),
        watermark=db.get_state(connection, HISTORY_KEY),
        full_sync_complete=db.get_state(connection, FULL_SYNC_DONE) == "1",
        newest=_when(int(rows["newest"] or 0)),
    )


@dataclass(frozen=True)
class SyncReport:
    mode: str
    added: int
    updated: int
    deleted: int
    relabelled: int
    recovered_gap: bool
    messages_total: int


def sync_mailbox(auth: AuthConfig, store_path: Path, label: str | None = "INBOX",
                 limit: int | None = None, progress: Progress = lambda _m: None) -> SyncReport:
    """Bring the local mailbox level with Gmail. The only operation that needs the network."""
    from mailai.client import build_service

    connection = db.open_store(store_path)
    service = build_service(load_credentials(auth))
    result = sync(service, connection, label=label, limit=limit, progress=progress)
    return SyncReport(
        mode=result.mode,
        added=result.added,
        updated=result.updated,
        deleted=result.deleted,
        relabelled=result.relabelled,
        recovered_gap=result.recovered_gap,
        messages_total=db.message_count(connection),
    )


@dataclass(frozen=True)
class ThreadSummary:
    thread_id: str
    subject: str
    started_by: str
    messages: int
    last: str


def list_threads(store_path: Path, limit: int = 20) -> list[ThreadSummary]:
    connection = db.open_store(store_path)
    return [
        ThreadSummary(
            thread_id=row["thread_id"],
            subject=row["subject"],
            started_by=row["started_by"],
            messages=row["messages"],
            last=_when(row["last_date"]),
        )
        for row in db.threads(connection, limit=limit)
    ]


@dataclass(frozen=True)
class ThreadDetail:
    thread_id: str
    subject: str
    messages: list[dict[str, Any]] = field(default_factory=list)


def read_thread(store_path: Path, thread_id: str) -> ThreadDetail:
    """A thread with quoted history already removed from each message.

    That removal is the difference between a readable thread and ten copies of its first
    message; the quoted text is still in the store if anything needs to check it.
    """
    connection = db.open_store(store_path)
    rows = db.thread_messages(connection, thread_id)
    if not rows:
        raise ValueError(f"no thread {thread_id} in {store_path}")
    return ThreadDetail(
        thread_id=thread_id,
        subject=rows[0].get("subject", ""),
        messages=[
            {
                "id": row["id"],
                "from": row["from_addr"],
                "to": row["to_addr"],
                "at": _when(row["internal_date"]),
                "labels": row["labels"],
                "body": row["body"],
            }
            for row in rows
        ],
    )


def search_mail(store_path: Path, text: str, limit: int = 20) -> list[dict[str, Any]]:
    connection = db.open_store(store_path)
    return [
        {**row, "at": _when(row.pop("internal_date"))}
        for row in db.search(connection, text, limit=limit)
    ]


def as_dict(value: object) -> dict:
    return asdict(value)  # type: ignore[arg-type]
