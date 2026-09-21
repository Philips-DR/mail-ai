"""A Gmail stand-in with just enough behaviour to exercise the sync.

Real enough to be worth testing against: it pages, it keeps a history stream, and it can be
told to expire a watermark the way Gmail does after about a week.
"""

from __future__ import annotations

import base64
from typing import Any


class HttpErrorStub(Exception):
    """Shaped like googleapiclient's HttpError enough for sync.is_gap to read it."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status


def encode(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def message(mid: str, thread: str, subject: str, body: str, date: int = 1000,
            labels: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": mid,
        "threadId": thread,
        "internalDate": str(date),
        "snippet": body[:40],
        "labelIds": labels or ["INBOX"],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "someone@example.com"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": subject},
            ],
            "body": {"data": encode(body)},
        },
    }


class _Request:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def execute(self) -> dict[str, Any]:
        return self._payload


class FakeGmail:
    """One mailbox, one history stream, and a switch to expire the watermark."""

    def __init__(self, messages: list[dict[str, Any]], history_id: str = "100") -> None:
        # State is named so it cannot collide with the API surface this fake also has to
        # present. `self.messages` would shadow the messages() method and `self.history`
        # would shadow history(); both surface as "'dict'/'list' object is not callable" a
        # long way from the cause. Bit twice while writing these tests.
        self.mailbox = {m["id"]: m for m in messages}
        self.history_id = history_id
        self.history_records: list[dict[str, Any]] = []
        self.expire_watermark = False
        self.fetched: list[str] = []
        self.page_size = 2  # small, so paging is actually exercised

    # -- the shape googleapiclient presents -------------------------------------------
    def users(self):
        return self

    def getProfile(self, userId: str):  # noqa: N803 - mirrors the real API
        return _Request({"historyId": self.history_id, "emailAddress": "me@example.com"})

    def messages(self):
        return _Messages(self)

    def history(self):
        return _History(self)


class _Messages:
    def __init__(self, gmail: FakeGmail) -> None:
        self.gmail = gmail

    def list(self, userId: str, labelIds=None, q=None, maxResults=None, _page: int = 0):  # noqa: N803
        ids = [
            m["id"] for m in self.gmail.mailbox.values()
            if not labelIds or set(labelIds) & set(m["labelIds"])
        ]
        size = self.gmail.page_size
        chunk = ids[_page * size : (_page + 1) * size]
        payload = {"messages": [{"id": i} for i in chunk]}
        if (_page + 1) * size < len(ids):
            payload["_next"] = {"labelIds": labelIds, "q": q, "_page": _page + 1}
        return _Request(payload)

    def list_next(self, request, response):
        nxt = response.get("_next")
        return self.list(userId="me", **nxt) if nxt else None

    def get(self, userId: str, id: str, format: str):  # noqa: A002, N803
        self.gmail.fetched.append(id)
        return _Request(self.gmail.mailbox[id])


class _History:
    def __init__(self, gmail: FakeGmail) -> None:
        self.gmail = gmail

    def list(self, userId: str, startHistoryId: str, labelId=None, maxResults=None):  # noqa: N803
        if self.gmail.expire_watermark:
            raise HttpErrorStub(404)
        return _Request({"history": list(self.gmail.history_records), "historyId": self.gmail.history_id})

    def list_next(self, request, response):
        return None
