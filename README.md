# mail-ai

A correct Gmail client with a hard send gate. **Not an email AI.**

Triage, drafting and summarising are judgment, and judgment lives in one declared seam.
What this owns is the mechanical half — incremental sync, thread reconstruction, MIME,
label state — because that is where being wrong is silent and expensive.

```bash
./mail auth                      # one-time browser consent
./mail sync                      # bring the local mailbox level with Gmail
./mail status                    # what is stored. Offline, no credentials
./mail threads                   # recent threads, newest first
./mail read <thread-id>          # one thread, quoted history removed
./mail search "claims"           # offline search of what you have
```

Everything but `sync` and `auth` works offline against the local SQLite mailbox.

## A mailbox is a log, not a folder

Gmail's own model is a history stream with labels projected over it. Treating it as a set
of folders to poll is what produces every duplicate, every missed message, and every
full-inbox rescan. Every rule below follows from that one fact.

**The first sync is full; every sync after it is incremental.** A full sync records the
watermark (`historyId`) *before* fetching anything — read afterwards, it would skip every
message that arrived during the fetch, because the listing has already paged past where
they would appear. Taking it first can only cause a message to be seen twice, and the
write is idempotent, so the worst case is redundant work rather than lost mail.

**An expired watermark is normal, not a failure.** Gmail keeps history records for about a
week. Past that the watermark returns 404 and there is no way to learn what was missed, so
the only correct answer is a full resync — and for a mailbox synced less often than weekly,
that is the expected path. Any other error is raised, because recovering from everything by
refetching the whole mailbox would hide real bugs behind a very expensive retry.

**A label change does not refetch the message.** Reading, archiving and starring are the
common case by a wide margin and none of them changes the message.

**An interrupted first sync resumes.** Re-listing ids is cheap and anything already stored
is skipped, so no extra bookkeeping is needed. The watermark is only written once the pass
completes, so an interrupted run correctly starts over rather than believing it finished.

## Threads you can actually read

A ten-message thread where every reply quotes the whole history is ten copies of the first
message. Left in, the same sentence is counted ten times by anything reading downstream,
and a summary of the thread is mostly its oldest message.

So each message is split into what it says and what it quoted. Both are stored — the quoted
half is how a mistake in reconstruction can still be checked, and discarding it would make
that unfalsifiable.

Handled: `>` quoting, the `On … wrote:` attribution (including when it wraps across lines),
Outlook's `-----Original Message-----` and `From:/Sent:/To:` header blocks, and the RFC 3676
`-- ` signature delimiter. Every one of these is a convention rather than a standard, so
each rule is written to be conservative: an em-dash in prose is not a signature, and a lone
`From:` line is not a quote.

## Scopes

`gmail.readonly`, and only that. Sending needs `gmail.send` and drafting needs
`gmail.compose`; those get added by the milestone that earns them, not in advance. A token
minted for a scope the code cannot yet use is an unforced risk, and widening a scope forces
a re-consent anyway, so nothing is saved by asking early.

## Setup

Needs an OAuth client of type **Desktop app** from Google Cloud, with the Gmail API enabled
on the project.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# save the client JSON as credentials.json, or point MAIL_AI_CREDENTIALS at it
./mail auth
```

Credentials are passed in, never discovered: `MAIL_AI_CREDENTIALS`, `MAIL_AI_TOKEN`,
`MAIL_AI_STORE`.

## Testing

```bash
.venv/bin/python -m pytest tests/ -q
```

No network, no credentials, no mailbox. The sync is tested against a fake Gmail that pages,
keeps a history stream, and can expire a watermark on demand — because "resync duplicates
nothing" and "an expired watermark recovers" are the two properties that cannot be checked
by reading the code.

## For a model to call

```bash
./mcp                            # MCP server on stdio
```

Five tools, the same operations the CLI uses: `status`, `sync`, `list_threads`,
`read_thread`, `search`. Only `sync` is marked as writing, and it writes to the local
mailbox file rather than to Gmail.

**This door opened before the send gate exists, and that is safe for a structural reason
rather than a careful one:** the OAuth scope is `gmail.readonly`, so the tool is incapable
of sending, replying or deleting. The capability is absent, not merely ungranted. When
drafting and sending arrive, they arrive with the gate.

Paths and credentials are injected: `MAIL_AI_STORE`, `MAIL_AI_CREDENTIALS`, `MAIL_AI_TOKEN`.

## Not built yet

Drafting, the send gate, and the MCP front door. The read path is deliberately complete and
trustworthy first: the whole thing can be built, tested and used before the tool is capable
of sending anything at all.
