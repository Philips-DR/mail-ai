# mail-ai — engineering rules

**A mailbox is a log, not a folder.** Gmail's model is a history stream with labels
projected over it. Every rule here follows from that; if a change starts treating the
mailbox as folders to poll, it is wrong before it is written.

**This is a Gmail client, not an email AI.** Triage, drafting and summarising are judgment
and belong in one declared seam above the store. Everything below it — sync, threading,
MIME, label state — is model-free, because a model that drifts on a message id sends mail
to the wrong person.

## The sync

- **Read the watermark BEFORE fetching, never after.** Afterwards skips every message that
  arrived during the fetch: the listing has paged past where they would appear, and a later
  watermark tells the next sync they were handled. Before can only cause a double-fetch,
  and the write is idempotent.
- **A 404 from `history.list` is a gap, not an error.** Gmail keeps history about a week;
  past that, full resync is the only correct answer. **Any other status is raised** —
  recovering from everything by refetching the whole mailbox hides real bugs behind a very
  expensive retry.
- **Write the watermark only after a full pass completes.** An interrupted run must start
  over rather than believe it finished.
- **Label changes never refetch.** They are the common case and they do not change the
  message.
- Resumability needs no extra state: re-list ids, skip what is already stored.
- **One watermark per label.** Gmail's history API takes a single label, so a shared
  watermark would let a sync of SENT tell the next sync of INBOX that its changes were
  handled. INBOX keeps the unsuffixed key so mailboxes synced before this existed do not
  silently full-resync.
- **SENT is synced by default, not just INBOX.** A sent message never carries INBOX, so
  INBOX-only meant sending mail the tool would never see again — a thread you replied to
  read as though you never answered. Only visible after actually sending one.

## MIME

- **Gmail strips base64url padding and the standard decoder rejects it.** Restore it, or
  every body whose length is not a multiple of four reads as empty.
- **Header names come back in inconsistent case.** Lowercase them on the way in.
- **An HTML-only message is stripped, not skipped.** Plenty of real senders never attach a
  plain alternative; skipping leaves silent holes in the mailbox.
- **A part with a filename is an attachment, not the body** — even when it is `text/plain`.
- Remove Gmail's `gmail_quote` wrapper *before* stripping tags, or the quoted history
  survives as unattributed prose.

## Live-verified against a real mailbox (2026-09-21)

First run on a real INBOX: 25 messages, 24 threads, full sync. Second run: incremental,
zero added, zero refetched. Gap recovery is unit-tested only — it needs a week-old
watermark to reproduce live.

Two things the real mailbox said that fixtures did not:

- **The HTML path is the main path, not the fallback.** 24 of 25 messages were
  Gmail-categorised automated mail, and **zero** messages came back with no extractable
  text — meaning `html_to_text` carried almost all of it. Treating HTML as a fallback is
  right by preference order and wrong by volume; it deserves the same care as the
  `text/plain` path, not less.
- **Extraction quality holds up.** Automated mail landed at ~3,800 chars over ~16 lines —
  roughly 236 characters per line, which is prose paragraphs rather than the short-line
  navigation soup that badly stripped HTML produces. Worth re-measuring the same way if the
  stripper is ever changed.

Quoted history was rare in this sample (1 message of 25), which is what an inbox of
automated mail looks like. It says nothing about the quoting rules; a mailbox of real
correspondence is still the test that matters for those.

## Quoting

Every rule is a convention, not a standard — only `-- ` is specified (RFC 3676). So each is
conservative, and the tests are mostly about what must NOT be cut: an em-dash in prose is
not a signature, a lone `From:` line is not a quote, and a subject beginning "Rebuild" is
not an accreted "Re:".

**Keep the quoted half.** It is how a mistake in reconstruction can be checked; discarding
it makes that unfalsifiable.

## Store

Threads are **derived by query, not stored**. A thread is not an independent fact — it is
whatever messages share a threadId — and a separate table is two things that can disagree
with no way to tell which is right.

## The send gate

- **`send` has no content parameter, and a test asserts it.** If `to` or `body` ever appears
  on it, compose-and-send becomes possible in one call and every other guarantee here is
  decoration.
- **Drafting and sending live in separate modules.** Nothing in `drafts.py` puts mail in
  front of anyone, so nothing there can be called by accident and have it leave.
- **The confirmation fingerprints content, not the draft id.** The id does not change when
  the draft does, so an id alone would authorise sending something nobody read. `bcc` is
  inside the fingerprint deliberately — silently adding a recipient is the edit a rendered
  review is least likely to reveal.
- A refusal is the mechanism working. It gets its own exception type and its own exit code
  so it never reads as a fault.
- **`review` must stay read-only.** If reading a draft needs approval, approving becomes two
  approvals and people stop reading the thing they are approving.

## Approval previews

`send_draft` and `discard_draft` declare, in MCP's `_meta`, that a caller should show
`review_draft`'s output before asking anyone to approve them. Their arguments are opaque
identifiers — `{draft_id, confirmation}` names no recipient and no subject — so a gate that
shows arguments shows nothing worth approving.

The declaration is advisory: a caller may ignore it, and one that honours it must still
check the named tool is read-only itself. What this side owes is that `review_draft` stays
read-only and keeps rendering every recipient it fingerprints, including `bcc`.

## Scopes

Narrowest that works, added by the milestone that earns them, and each in **its own token
file** so a read-only run cannot silently pick up a send-capable credential.

**`gmail.compose` grants sending, not just drafting** — "Manage drafts and send emails",
and there is no draft-only scope. The scope therefore cannot separate drafting from
sending; only the code can. This was checked against Google's scope reference before the
design was written, and it inverted the plan: the original sequencing assumed compose for
M2 and send for M3 would provide the separation, and it provides none.

**Only `auth` may open a browser.** `load_credentials` raises unless `interactive=True`, and
only the auth command passes it. Found the hard way: `send` with no cached token launched a
consent flow and hung. From the MCP door that would block a model's tool call on a window
nobody is looking at.

## Testing

No network, no credentials, no mailbox. The sync is tested against a fake Gmail that pages
and keeps a history stream.

**Name fake state so it cannot collide with the API surface it also presents.** `self.messages`
shadows `messages()` and `self.history` shadows `history()`; both surface as
"'dict' object is not callable" a long way from the cause. That bit twice in one sitting.
