"""Building an RFC 822 message, and the fingerprint the send gate turns on.

Pure: no network, no credentials, no Gmail. Everything here is a function of its arguments,
which is what lets the gate's behaviour be tested exhaustively without a mailbox.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any


@dataclass(frozen=True)
class Composition:
    """What a draft says. The unit the gate fingerprints."""

    to: str
    subject: str
    body: str
    cc: str = ""
    bcc: str = ""
    # Set when replying, so other clients thread it correctly (see build_raw).
    in_reply_to: str = ""
    references: str = ""

    def fingerprint(self) -> str:
        """A hash of everything that would actually be sent.

        This is what makes "you reviewed it" mean something. A draft id alone is not
        enough: a draft can be edited between the moment it was shown to someone and the
        moment send is called, and the id does not change when it is. Fingerprinting the
        content means the gate refuses a draft that is no longer the one that was read.

        bcc is included deliberately -- silently adding a recipient after review is exactly
        the edit this is meant to catch.
        """
        material = "\x00".join([
            self.to.strip(), self.cc.strip(), self.bcc.strip(),
            self.subject.strip(), self.body.strip(),
        ])
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def build_raw(composition: Composition) -> str:
    """An RFC 822 message, base64url-encoded the way Gmail's `raw` field wants it.

    In-Reply-To and References carry the ORIGINAL message's RFC 822 Message-ID, which is
    not the same thing as Gmail's own message id. Setting Gmail's `threadId` alone makes a
    reply thread correctly in Gmail's own UI and nowhere else -- every other mail client
    threads on these headers, so a reply sent without them arrives as a new conversation
    for anyone not using Gmail.
    """
    message = EmailMessage()
    message["To"] = composition.to
    if composition.cc:
        message["Cc"] = composition.cc
    if composition.bcc:
        message["Bcc"] = composition.bcc
    message["Subject"] = composition.subject
    if composition.in_reply_to:
        message["In-Reply-To"] = composition.in_reply_to
        # References is the whole chain; falling back to In-Reply-To gives a one-link
        # chain, which is correct for a reply to a thread's first message.
        message["References"] = composition.references or composition.in_reply_to
    message.set_content(composition.body)

    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def reply_subject(original: str) -> str:
    """"Re: x", and never "Re: Re: x" -- accretion is what normalise_subject undoes."""
    stripped = original.strip()
    return stripped if stripped.lower().startswith("re:") else f"Re: {stripped}"


def draft_body(draft: dict[str, Any]) -> Composition:
    """Read a Gmail draft resource back into the Composition it represents.

    The gate fingerprints THIS, recomputed from the draft's current state at send time,
    rather than anything remembered from when it was created.
    """
    from mailai.mime import body_text, headers

    payload = draft.get("message", {}).get("payload", {}) or {}
    head = headers(payload)
    return Composition(
        to=head.get("to", ""),
        cc=head.get("cc", ""),
        bcc=head.get("bcc", ""),
        subject=head.get("subject", ""),
        body=body_text(payload),
        in_reply_to=head.get("in-reply-to", ""),
        references=head.get("references", ""),
    )
