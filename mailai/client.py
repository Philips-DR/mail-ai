"""A thin wrapper over the Gmail API. No judgment, no caching, no retries beyond the
library's own -- just the four calls the sync needs, named for what they do.
"""

from __future__ import annotations

from typing import Any, Iterator

# Gmail's own ceiling for these list calls. Asking for more is silently clamped, which
# makes a paging bug look like a short mailbox.
PAGE_SIZE = 500


def build_service(credentials):
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


def profile(service) -> dict[str, Any]:
    """Includes historyId -- the watermark every incremental sync starts from."""
    return service.users().getProfile(userId="me").execute()


def list_message_ids(service, label: str | None = None, query: str | None = None) -> Iterator[str]:
    """Every message id matching a label or query, paged to exhaustion."""
    request = service.users().messages().list(
        userId="me",
        labelIds=[label] if label else None,
        q=query,
        maxResults=PAGE_SIZE,
    )
    while request is not None:
        response = request.execute()
        for message in response.get("messages", []) or []:
            yield str(message["id"])
        request = service.users().messages().list_next(request, response)


def get_message(service, message_id: str) -> dict[str, Any]:
    """format=full gives headers and the decoded part tree without the raw RFC822 blob."""
    return service.users().messages().get(userId="me", id=message_id, format="full").execute()


def list_history(
    service, start_history_id: str, label: str | None = None
) -> tuple[list[dict[str, Any]], str]:
    """Changes since a watermark, and the new watermark to store.

    Returns both together on purpose. The new watermark must come from the LAST history
    response, not from a separate getProfile call afterwards: anything arriving between the
    two would be skipped forever, because the next sync would start after it.

    Raises googleapiclient.errors.HttpError with status 404 when the watermark is older
    than Gmail's retention for history records. That is not an error to report -- it is the
    documented signal to fall back to a full resync, and sync.py handles it there.
    """
    records: list[dict[str, Any]] = []
    latest = start_history_id
    request = service.users().history().list(
        userId="me",
        startHistoryId=start_history_id,
        labelId=label,
        maxResults=PAGE_SIZE,
    )
    while request is not None:
        response = request.execute()
        records.extend(response.get("history", []) or [])
        latest = str(response.get("historyId") or latest)
        request = service.users().history().list_next(request, response)
    return records, latest
