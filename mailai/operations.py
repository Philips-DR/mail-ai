"""What mail-ai can be asked to do, as data in and data out.

Both front doors sit on this. Nothing here writes to stdout or sets an exit code -- that is
a front door's job -- and nothing here makes a judgment about an email. Triage, drafting
and summarising are a later milestone and land in one declared seam above this.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mailai import store as db
from mailai.auth import AuthConfig, NotAuthorised, load_credentials
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


# INBOX alone leaves a hole: a message you SENT never carries the INBOX label, so the tool
# could send mail it would never see again and a thread you replied to would read as though
# you never answered. Found by actually sending one.
DEFAULT_LABELS: tuple[str, ...] = ("INBOX", "SENT")


@dataclass(frozen=True)
class SyncReport:
    mode: str
    added: int
    updated: int
    deleted: int
    relabelled: int
    recovered_gap: bool
    messages_total: int
    per_label: dict[str, str] = field(default_factory=dict)


def sync_mailbox(auth: AuthConfig, store_path: Path,
                 labels: Sequence[str | None] = DEFAULT_LABELS,
                 limit: int | None = None,
                 progress: Progress = lambda _m: None) -> SyncReport:
    """Bring the local mailbox level with Gmail. The only operation that needs the network.

    One pass per label, because Gmail's history API takes one label at a time and each
    therefore carries its own watermark. A message with two of them is simply upserted
    twice, which the store is built to survive.
    """
    from mailai.client import build_service

    connection = db.open_store(store_path)
    service = build_service(load_credentials(auth))

    totals = {"added": 0, "updated": 0, "deleted": 0, "relabelled": 0}
    modes: dict[str, str] = {}
    recovered = False

    for label in labels:
        name = label or "all"
        progress(f"   {name}")
        result = sync(service, connection, label=label, limit=limit, progress=progress)
        for key in totals:
            totals[key] += getattr(result, key)
        modes[name] = result.mode
        recovered = recovered or result.recovered_gap

    return SyncReport(
        mode="+".join(sorted(set(modes.values()))) or "none",
        **totals,
        recovered_gap=recovered,
        messages_total=db.message_count(connection),
        per_label=modes,
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


# ---------------------------------------------------------------------------
# Drafting and sending
# ---------------------------------------------------------------------------

class NeedsComposeScope(Exception):
    """A read-only token cannot draft. Raised before any network call is made."""


def _compose_service(auth: AuthConfig):
    """Every drafting operation goes through here, so the scope check cannot be skipped."""
    from mailai.client import build_service

    if not auth.can_send:
        raise NeedsComposeScope(
            "this needs a drafting token. Run: ./mail auth --compose\n"
            "  Note that Gmail's compose scope also grants sending; the send gate, not the "
            "scope, is what stops mail leaving."
        )
    return build_service(load_credentials(auth))


@dataclass(frozen=True)
class DraftReport:
    draft_id: str
    to: str
    subject: str
    thread_id: str
    confirmation: str


def _report(draft_id: str, composition, thread_id: str) -> DraftReport:
    return DraftReport(
        draft_id=draft_id,
        to=composition.to,
        subject=composition.subject,
        thread_id=thread_id,
        # Minted here so the caller that just wrote the draft can send it without a second
        # read -- but it is still a fingerprint of content, so it stops working the moment
        # anything edits the draft.
        confirmation=composition.fingerprint(),
    )


def draft_new(auth: AuthConfig, to: str, subject: str, body: str,
              cc: str = "", bcc: str = "") -> DraftReport:
    """Write a new draft. Creates nothing in anyone else's mailbox."""
    from mailai import drafts
    from mailai.compose import Composition

    service = _compose_service(auth)
    composition = Composition(to=to, subject=subject, body=body, cc=cc, bcc=bcc)
    created = drafts.create(service, composition)
    return _report(str(created["id"]), composition, "")


def draft_reply(auth: AuthConfig, store_path: Path, thread_id: str, body: str) -> DraftReport:
    """Reply in an existing thread, threaded correctly for clients that are not Gmail.

    The recipient, subject and Message-ID all come from the local store rather than a
    fresh fetch -- the sync already has them, and reading them offline means a draft can be
    written from what was synced rather than from whatever the mailbox looks like now.
    """
    from mailai import drafts
    from mailai.compose import Composition, reply_subject

    service = _compose_service(auth)
    connection = db.open_store(store_path)
    messages = db.thread_messages(connection, thread_id)
    if not messages:
        raise ValueError(f"no thread {thread_id} in {store_path}. Sync first.")

    last = messages[-1]
    composition = Composition(
        to=last["from_addr"],
        subject=reply_subject(last.get("subject", "")),
        body=body,
        in_reply_to=last.get("rfc822_message_id") or "",
    )
    created = drafts.create(service, composition, thread_id=thread_id)
    return _report(str(created["id"]), composition, thread_id)


@dataclass(frozen=True)
class ReviewReport:
    draft_id: str
    thread_id: str
    rendered: str
    confirmation: str


def review_draft(auth: AuthConfig, draft_id: str) -> ReviewReport:
    """Read a draft as it currently stands, and mint the token that authorises sending it."""
    from mailai import sending

    reviewed = sending.review(_compose_service(auth), draft_id)
    return ReviewReport(
        draft_id=reviewed.draft_id,
        thread_id=reviewed.thread_id,
        rendered=reviewed.rendered(),
        confirmation=reviewed.confirmation,
    )


def list_drafts(auth: AuthConfig, limit: int = 25) -> list[dict[str, Any]]:
    from mailai import drafts

    service = _compose_service(auth)
    return [
        {"draft_id": str(d.get("id", "")), "thread_id": str(d.get("message", {}).get("threadId", ""))}
        for d in drafts.list_all(service, limit=limit)
    ]


def discard_draft(auth: AuthConfig, draft_id: str) -> dict[str, str]:
    from mailai import drafts

    drafts.delete(_compose_service(auth), draft_id)
    return {"discarded": draft_id}


@dataclass(frozen=True)
class SendReport:
    draft_id: str
    message_id: str
    thread_id: str


def send_draft(auth: AuthConfig, draft_id: str, confirmation: str) -> SendReport:
    """Send a draft that still says what was reviewed. The only operation that leaves."""
    from mailai import sending

    sent = sending.send(_compose_service(auth), draft_id, confirmation)
    return SendReport(
        draft_id=draft_id,
        message_id=str(sent.get("id", "")),
        thread_id=str(sent.get("threadId", "")),
    )
