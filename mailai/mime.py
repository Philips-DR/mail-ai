"""Gmail payloads to plain text.

A Gmail message is a tree of MIME parts, not a body. The text you want may be at the root,
one level down beside an HTML alternative, or several levels down past an attachment
wrapper -- and the encoding is base64url with the padding stripped, which the standard
decoder rejects unless you put it back.

Pure functions over payload dicts. Nothing here touches the network.
"""

from __future__ import annotations

import base64
import binascii
import re
from html import unescape
from typing import Any

PREFERRED = ("text/plain", "text/html")


def decode_body(data: str) -> str:
    """Decode Gmail's base64url body data.

    Gmail strips the "=" padding. base64.urlsafe_b64decode requires it, so it must be
    restored -- without this, any body whose length is not a multiple of four raises
    binascii.Error and the message reads as empty.
    """
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        return ""


def headers(payload: dict[str, Any]) -> dict[str, str]:
    """Header names lowercased: Gmail is inconsistent about their case between calls."""
    return {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in payload.get("headers", []) or []
    }


def _walk(payload: dict[str, Any]) -> list[dict[str, Any]]:
    parts = payload.get("parts")
    if not parts:
        return [payload]
    out: list[dict[str, Any]] = []
    for part in parts:
        out.extend(_walk(part))
    return out


def html_to_text(html: str) -> str:
    """Enough HTML stripping for an email body, and no more.

    Gmail's own quote wrapper is a <div class="gmail_quote">; it is removed before tags so
    that quoted history does not survive as unattributed prose once the markup is gone.
    """
    without_quote = re.sub(
        r'<(blockquote|div)[^>]*class="[^"]*gmail_quote[^"]*"[^>]*>.*',
        "",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    without_invisible = re.sub(
        r"<(script|style)\b.*?</\1>", " ", without_quote, flags=re.IGNORECASE | re.DOTALL
    )
    # <br> is a line break; </p> and friends end a block, which reads as a blank line.
    # Collapsing both to "\n" runs paragraphs together and makes a wall of text.
    with_lines = re.sub(r"<br\s*/?>", "\n", without_invisible, flags=re.IGNORECASE)
    with_breaks = re.sub(r"</(p|div|tr|h[1-6]|li)>", "\n\n", with_lines, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", with_breaks)
    return re.sub(r"\n{3,}", "\n\n", unescape(text)).strip()


def body_text(payload: dict[str, Any]) -> str:
    """The best plain-text rendering of a message payload.

    text/plain wins when present. An HTML-only message is stripped rather than skipped --
    plenty of real senders never attach a plain alternative, and dropping them would leave
    silent holes in a mailbox.
    """
    parts = _walk(payload)
    by_type: dict[str, str] = {}
    for part in parts:
        mime_type = str(part.get("mimeType", ""))
        if mime_type not in PREFERRED or part.get("filename"):
            continue
        text = decode_body(str(part.get("body", {}).get("data", "")))
        if text and mime_type not in by_type:
            by_type[mime_type] = text

    if "text/plain" in by_type:
        return by_type["text/plain"].strip()
    if "text/html" in by_type:
        return html_to_text(by_type["text/html"])
    return ""


def attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Filenames and sizes only. Bytes are fetched on demand, never during a sync."""
    return [
        {
            "filename": part.get("filename", ""),
            "mime_type": part.get("mimeType", ""),
            "size": int(part.get("body", {}).get("size", 0) or 0),
            "attachment_id": part.get("body", {}).get("attachmentId"),
        }
        for part in _walk(payload)
        if part.get("filename")
    ]
