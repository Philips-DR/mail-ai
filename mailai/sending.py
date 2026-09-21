"""The send gate.

**There is no function here that takes message content.** `send` takes a draft id and a
confirmation, and nothing else — so composing and sending in one step is not something a
caller has to remember not to do, it is something the API of this module does not offer.

That matters more than it looks, because the scope cannot help. Gmail's `gmail.compose` is
documented as "Manage drafts and send emails": there is no draft-only scope, so the moment
this tool can create a draft its token can also send. The separation is entirely in the
code, which is why it is structural here rather than a confirmation prompt.

The confirmation is a fingerprint of what the draft said when it was read. Recomputed from
the draft's current state at send time, it answers a question a draft id alone cannot:
not "did someone approve sending this draft" but "did someone approve sending *this*".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mailai import drafts
from mailai.compose import Composition, draft_body


class SendRefused(Exception):
    """The gate declined. Never a bug: it is the mechanism working."""


class NotReviewed(SendRefused):
    """No confirmation supplied. Read the draft first."""


class DraftChanged(SendRefused):
    """The draft is not the one that was reviewed."""


@dataclass(frozen=True)
class Review:
    """A draft as it currently stands, and the token that authorises sending exactly this."""

    draft_id: str
    composition: Composition
    confirmation: str
    thread_id: str = ""

    def rendered(self) -> str:
        c = self.composition
        lines = [f"To: {c.to}"]
        if c.cc:
            lines.append(f"Cc: {c.cc}")
        if c.bcc:
            lines.append(f"Bcc: {c.bcc}")
        lines += [f"Subject: {c.subject}", "", c.body]
        return "\n".join(lines)


def review(service, draft_id: str) -> Review:
    """Read a draft and mint the confirmation that would authorise sending it."""
    resource = drafts.get(service, draft_id)
    composition = draft_body(resource)
    return Review(
        draft_id=draft_id,
        composition=composition,
        confirmation=composition.fingerprint(),
        thread_id=str(resource.get("message", {}).get("threadId", "")),
    )


def send(service, draft_id: str, confirmation: str) -> dict[str, Any]:
    """Send an existing draft, and only if it still says what was reviewed.

    Deliberately has no `to`, `subject` or `body` parameter. The only way to put words in
    an email with this tool is to create a draft, and the only way to send one is to have
    read it in the state it is now in.
    """
    if not confirmation or not confirmation.strip():
        raise NotReviewed(
            f"draft {draft_id} has not been reviewed. Read it first, and pass the "
            f"confirmation that review returns."
        )

    current = review(service, draft_id)
    if confirmation.strip() != current.confirmation:
        raise DraftChanged(
            f"draft {draft_id} has changed since it was reviewed and was not sent. "
            f"Read it again and confirm the new version."
        )

    return service.users().drafts().send(userId="me", body={"id": draft_id}).execute()
