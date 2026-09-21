"""Keeping a local mailbox level with Gmail.

**A mailbox is a log, not a folder.** Gmail's own model is a history stream with labels
projected over it, and treating it as a set of folders to poll is what produces every
duplicate, every missed message, and every full-inbox rescan. Everything below follows from
that one fact.

Two modes. A full sync when there is no watermark, and an incremental sync from the
watermark otherwise. The incremental path is the normal one; the full path exists for the
first run and for recovering a gap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from mailai import client as gmail
from mailai.mime import attachments, body_text, headers
from mailai.quoting import normalise_subject, split_reply
from mailai.store import (
    delete_message,
    get_state,
    has_message,
    labels_of,
    set_labels,
    set_state,
    upsert_message,
)

HISTORY_KEY = "history_id"
FULL_SYNC_DONE = "full_sync_complete"

Progress = Callable[[str], None]


@dataclass
class SyncResult:
    mode: str
    added: int = 0
    updated: int = 0
    deleted: int = 0
    relabelled: int = 0
    history_id: str = ""
    recovered_gap: bool = False

    def summary(self) -> str:
        parts = [f"{self.added} added", f"{self.updated} updated"]
        if self.deleted:
            parts.append(f"{self.deleted} deleted")
        if self.relabelled:
            parts.append(f"{self.relabelled} relabelled")
        return ", ".join(parts)


def to_row(message: dict[str, Any]) -> dict[str, Any]:
    """One Gmail message as a store row. Pure: no network, no database."""
    payload = message.get("payload", {}) or {}
    head = headers(payload)
    text = body_text(payload)
    body, quoted = split_reply(text)
    subject = head.get("subject", "")
    return {
        "id": str(message.get("id", "")),
        "thread_id": str(message.get("threadId", "")),
        "internal_date": int(message.get("internalDate", 0) or 0),
        "from_addr": head.get("from", ""),
        "to_addr": head.get("to", ""),
        "cc_addr": head.get("cc", ""),
        "subject": subject,
        "normalised_subject": normalise_subject(subject),
        "snippet": str(message.get("snippet", "")),
        "body": body,
        "quoted": quoted,
        "labels": list(message.get("labelIds", []) or []),
        "attachments": attachments(payload),
        # Kept so a reply can carry In-Reply-To/References. Gmail's own message id is not
        # a substitute: every non-Gmail client threads on this header.
        "rfc822_message_id": head.get("message-id", ""),
    }


def _store_message(service, connection, message_id: str, result: SyncResult) -> None:
    existed = has_message(connection, message_id)
    upsert_message(connection, to_row(gmail.get_message(service, message_id)))
    if existed:
        result.updated += 1
    else:
        result.added += 1


def full_sync(service, connection, label: str | None = None, limit: int | None = None,
              progress: Progress = lambda _m: None) -> SyncResult:
    """Fetch everything matching a label, then record the watermark.

    The watermark is read BEFORE any message is fetched. Reading it afterwards would skip
    every message that arrived during the fetch: the listing has already paged past where
    they would appear, and a later watermark would tell the next sync they were already
    handled. Taking it first can only cause a message to be seen twice, and the upsert is
    idempotent, so the worst case is redundant work rather than lost mail.
    """
    result = SyncResult(mode="full")
    watermark = str(gmail.profile(service).get("historyId", ""))

    # Resumability without extra bookkeeping: re-listing ids is cheap, and anything already
    # stored is skipped. An interrupted run leaves no watermark, so it simply starts over
    # and finishes the part it had not reached.
    seen = 0
    for message_id in gmail.list_message_ids(service, label=label):
        if limit is not None and seen >= limit:
            break
        seen += 1
        if has_message(connection, message_id):
            continue
        _store_message(service, connection, message_id, result)
        if result.added % 25 == 0:
            connection.commit()
            progress(f"   {result.added} fetched")

    set_state(connection, HISTORY_KEY, watermark)
    set_state(connection, FULL_SYNC_DONE, "1")
    connection.commit()
    result.history_id = watermark
    return result


def incremental_sync(service, connection, label: str | None = None,
                     progress: Progress = lambda _m: None) -> SyncResult:
    """Apply every change since the watermark. Raises if the watermark is too old."""
    result = SyncResult(mode="incremental")
    watermark = get_state(connection, HISTORY_KEY) or ""
    records, latest = gmail.list_history(service, watermark, label=label)

    for record in records:
        for added in record.get("messagesAdded", []) or []:
            _store_message(service, connection, str(added["message"]["id"]), result)

        for removed in record.get("messagesDeleted", []) or []:
            delete_message(connection, str(removed["message"]["id"]))
            result.deleted += 1

        # Label changes are the common case by a wide margin -- reading, archiving and
        # starring all land here -- and none of them needs the message refetched.
        for change in record.get("labelsAdded", []) or []:
            message_id = str(change["message"]["id"])
            if has_message(connection, message_id):
                set_labels(connection, message_id,
                           labels_of(connection, message_id) + list(change.get("labelIds", [])))
                result.relabelled += 1
        for change in record.get("labelsRemoved", []) or []:
            message_id = str(change["message"]["id"])
            if has_message(connection, message_id):
                gone = set(change.get("labelIds", []))
                set_labels(connection, message_id,
                           [l for l in labels_of(connection, message_id) if l not in gone])
                result.relabelled += 1

    set_state(connection, HISTORY_KEY, latest)
    connection.commit()
    result.history_id = latest
    return result


def is_gap(error: Exception) -> bool:
    """Is this the "your watermark is too old" signal?

    Gmail keeps history records for about a week. Past that the watermark returns 404, and
    the only correct response is a full resync -- there is no way to learn what was missed.
    Treated as a normal state rather than a failure, because for any mailbox synced less
    often than weekly it is the expected one.
    """
    status = getattr(error, "status_code", None) or getattr(
        getattr(error, "resp", None), "status", None
    )
    return int(status or 0) == 404


def sync(service, connection, label: str | None = None, limit: int | None = None,
         progress: Progress = lambda _m: None) -> SyncResult:
    """The one entry point: incremental when possible, full when necessary."""
    if get_state(connection, HISTORY_KEY) and get_state(connection, FULL_SYNC_DONE):
        try:
            return incremental_sync(service, connection, label=label, progress=progress)
        except Exception as error:
            if not is_gap(error):
                raise
            progress("   watermark too old — falling back to a full resync")
            result = full_sync(service, connection, label=label, limit=limit, progress=progress)
            result.recovered_gap = True
            return result

    return full_sync(service, connection, label=label, limit=limit, progress=progress)
