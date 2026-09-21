"""Draft management. Creating, reading, editing and discarding — never sending.

Sending lives in sending.py behind the gate, and it is kept in a separate module on
purpose: there is no function in this file that puts mail in front of a person, so nothing
here can be called by accident and have it leave.
"""

from __future__ import annotations

from typing import Any

from mailai.compose import Composition, build_raw, draft_body


def create(service, composition: Composition, thread_id: str | None = None) -> dict[str, Any]:
    """Create a draft. Returns the Gmail draft resource, including its id."""
    message: dict[str, Any] = {"raw": build_raw(composition)}
    if thread_id:
        # Gmail's own threading. The In-Reply-To/References headers inside the raw message
        # are what make it thread anywhere else -- see compose.build_raw.
        message["threadId"] = thread_id
    return service.users().drafts().create(userId="me", body={"message": message}).execute()


def update(service, draft_id: str, composition: Composition,
           thread_id: str | None = None) -> dict[str, Any]:
    """Replace a draft's content. The draft id survives, which is why the send gate
    fingerprints content rather than trusting the id."""
    message: dict[str, Any] = {"raw": build_raw(composition)}
    if thread_id:
        message["threadId"] = thread_id
    return service.users().drafts().update(
        userId="me", id=draft_id, body={"message": message}
    ).execute()


def get(service, draft_id: str) -> dict[str, Any]:
    return service.users().drafts().get(userId="me", id=draft_id, format="full").execute()


def read(service, draft_id: str) -> Composition:
    """What this draft currently says, as the gate will fingerprint it."""
    return draft_body(get(service, draft_id))


def list_all(service, limit: int = 25) -> list[dict[str, Any]]:
    response = service.users().drafts().list(userId="me", maxResults=limit).execute()
    return list(response.get("drafts", []) or [])


def delete(service, draft_id: str) -> None:
    service.users().drafts().delete(userId="me", id=draft_id).execute()
