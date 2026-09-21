"""Separate what someone actually wrote from what their client quoted back.

This is the difference between a mailbox you can read and one you cannot. A ten-message
thread where every reply carries the whole history is ten copies of the first message; left
in, the same sentence is counted ten times by anything reading downstream, and a summary of
the thread is mostly the oldest message in it.

Pure functions over strings. Every rule here is a convention rather than a standard -- only
the "-- " signature delimiter is actually specified (RFC 3676) -- so each is written to be
conservative: when a marker is ambiguous, the text stays.
"""

from __future__ import annotations

import re

# RFC 3676: a line containing exactly "-- " separates a signature from the body. The
# trailing space is part of the spec and many clients drop it, so a bare "--" is accepted
# too -- but only as a whole line, or every em-dash in prose would truncate the message.
SIGNATURE = re.compile(r"^--\s?$")

# "On Mon, 1 Sep 2026 at 10:04, Someone <x@y> wrote:" -- possibly wrapped across two lines,
# which is why the terminator is searched for rather than anchored to the same line.
ATTRIBUTION = re.compile(r"^\s*On\b.{0,200}?\bwrote:\s*$", re.DOTALL)
ATTRIBUTION_OPEN = re.compile(r"^\s*On\b.*,\s*$")

# Outlook and older clients announce the quote with a divider or a header block instead.
DIVIDER = re.compile(r"^\s*(-{3,}\s*(Original Message|Forwarded message)\s*-{3,}|_{10,})\s*$",
                     re.IGNORECASE)
OUTLOOK_HEADER = re.compile(r"^\s*From:\s.+$", re.IGNORECASE)


def _is_quoted(line: str) -> bool:
    return line.lstrip().startswith(">")


def split_reply(text: str) -> tuple[str, str]:
    """Return (what this message says, everything it quoted or signed off with).

    The quoted part is kept rather than discarded: it is how a thread's history can still be
    inspected when reconstruction gets something wrong, and throwing it away would make that
    unfalsifiable.
    """
    lines = text.splitlines()
    cut = len(lines)

    for index, line in enumerate(lines):
        if SIGNATURE.match(line) or DIVIDER.match(line):
            cut = index
            break
        if _is_quoted(line):
            # Walk back over the attribution line and any blank lines above the quote, so
            # "On Monday, X wrote:" does not survive as a dangling sentence.
            start = index
            while start > 0 and not lines[start - 1].strip():
                start -= 1
            if start > 0 and (
                ATTRIBUTION.match(lines[start - 1]) or ATTRIBUTION_OPEN.match(lines[start - 1])
            ):
                start -= 1
                while start > 0 and ATTRIBUTION_OPEN.match(lines[start - 1]):
                    start -= 1
            cut = start
            break
        if ATTRIBUTION.match(line):
            cut = index
            break
        # A "From:" header only means a quote when it opens a block of them; on its own it
        # is as likely to be someone typing about a sender.
        if OUTLOOK_HEADER.match(line) and index + 1 < len(lines):
            following = " ".join(lines[index + 1 : index + 4]).lower()
            if "sent:" in following or "subject:" in following or "to:" in following:
                cut = index
                break

    body = "\n".join(lines[:cut]).strip()
    quoted = "\n".join(lines[cut:]).strip()
    return body, quoted


def strip_quoted(text: str) -> str:
    """Just what this message says."""
    return split_reply(text)[0]


def normalise_subject(subject: str) -> str:
    """Drop the Re:/Fwd: accretion so a thread's subject is stable across its replies."""
    previous = None
    current = subject.strip()
    while current != previous:
        previous = current
        current = re.sub(r"^\s*(re|fwd|fw|aw|sv)\s*(\[\d+\])?\s*:\s*", "", current, flags=re.IGNORECASE)
    return current.strip()
