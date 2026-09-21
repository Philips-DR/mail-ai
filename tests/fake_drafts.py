"""A Gmail drafts stand-in, enough to drive the send gate.

State is named so it cannot collide with the API surface this also presents -- `store`, not
`drafts`, because `self.drafts` would shadow the drafts() method and surface as
"'dict' object is not callable" a long way from the cause.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any


class _Request:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def execute(self) -> Any:
        return self._payload


def _payload_for(to: str, subject: str, body: str) -> dict[str, Any]:
    return {
        "mimeType": "text/plain",
        "headers": [
            {"name": "To", "value": to},
            {"name": "Subject", "value": subject},
        ],
        "body": {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")},
    }


class FakeDrafts:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, Any]] = {}
        self.sent: list[str] = []
        self._next = 1

    def users(self):
        return self

    def drafts(self):
        return _Drafts(self)

    def add(self, to: str, subject: str, body: str, thread_id: str = "") -> str:
        draft_id = f"d{self._next}"
        self._next += 1
        self.store[draft_id] = {
            "id": draft_id,
            "message": {"threadId": thread_id, "payload": _payload_for(to, subject, body)},
        }
        return draft_id

    def edit(self, draft_id: str, to: str, subject: str, body: str) -> None:
        """Change a draft in place, exactly as a person editing it in Gmail would."""
        self.store[draft_id]["message"]["payload"] = _payload_for(to, subject, body)


class _Drafts:
    def __init__(self, gmail: FakeDrafts) -> None:
        self.gmail = gmail

    def create(self, userId: str, body: dict):  # noqa: N803
        raw = base64.urlsafe_b64decode(body["message"]["raw"])
        parsed = EmailMessage()
        import email

        parsed = email.message_from_bytes(raw)
        draft_id = self.gmail.add(
            parsed.get("To", ""), parsed.get("Subject", ""),
            parsed.get_payload(decode=True).decode() if not parsed.is_multipart() else "",
            body["message"].get("threadId", ""),
        )
        return _Request(self.gmail.store[draft_id])

    def get(self, userId: str, id: str, format: str = "full"):  # noqa: A002, N803
        if id not in self.gmail.store:
            raise KeyError(f"no draft {id}")
        return _Request(self.gmail.store[id])

    def list(self, userId: str, maxResults: int = 25):  # noqa: N803
        return _Request({"drafts": list(self.gmail.store.values())[:maxResults]})

    def delete(self, userId: str, id: str):  # noqa: A002, N803
        self.gmail.store.pop(id, None)
        return _Request(None)

    def send(self, userId: str, body: dict):  # noqa: N803
        draft_id = body["id"]
        self.gmail.sent.append(draft_id)
        return _Request({"id": f"sent-{draft_id}", "threadId": "t1"})
