"""./mail -- the human front door."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mailai.auth import AuthConfig, MissingCredentials, NotAuthorised, authorize, has_token
from mailai.operations import (
    NeedsComposeScope,
    discard_draft,
    draft_new,
    draft_reply,
    list_drafts,
    list_threads,
    read_thread,
    review_draft,
    search_mail,
    send_draft,
    status,
    sync_mailbox,
)
from mailai.sending import SendRefused

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STORE = Path(os.environ.get("MAIL_AI_STORE", ROOT / "mailbox.sqlite3"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="A correct Gmail client. Read-only, for now.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-s", "--store", default=str(DEFAULT_STORE), help="The local mailbox")
    sub = parser.add_subparsers(dest="command", required=True)

    a = sub.add_parser("auth", help="Authorise against Gmail and cache a refresh token")
    a.add_argument("--compose", action="store_true",
                   help="Also request drafting. Gmail's compose scope grants SENDING too; "
                        "the send gate, not the scope, is what stops mail leaving")
    sub.add_parser("status", help="What the local mailbox holds. Offline, no credentials")

    s = sub.add_parser("sync", help="Bring the local mailbox level with Gmail")
    s.add_argument("-l", "--label", default="INBOX", help="Label to sync. 'all' for everything")
    s.add_argument("-n", "--limit", type=int, default=None,
                   help="Stop after this many messages on a full sync")

    t = sub.add_parser("threads", help="Recent threads, newest first")
    t.add_argument("-n", "--limit", type=int, default=20)

    r = sub.add_parser("read", help="One thread, with quoted history removed")
    r.add_argument("thread_id")

    f = sub.add_parser("search", help="Search the local mailbox. Offline")
    f.add_argument("text")
    f.add_argument("-n", "--limit", type=int, default=20)

    d = sub.add_parser("draft", help="Write a new draft. Sends nothing")
    d.add_argument("--to", required=True)
    d.add_argument("--subject", required=True)
    d.add_argument("--body", required=True)
    d.add_argument("--cc", default="")

    rp = sub.add_parser("reply", help="Draft a reply in a thread. Sends nothing")
    rp.add_argument("thread_id")
    rp.add_argument("--body", required=True)

    sub.add_parser("drafts", help="List drafts")

    rv = sub.add_parser("review", help="Read a draft and print the token that would send it")
    rv.add_argument("draft_id")

    sd = sub.add_parser("send", help="Send a reviewed draft. The only command that leaves")
    sd.add_argument("draft_id")
    sd.add_argument("--confirm", required=True,
                    help="The confirmation printed by `review`. Sending without it, or with "
                         "a stale one, is refused")

    dd = sub.add_parser("discard", help="Delete a draft")
    dd.add_argument("draft_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store_path = Path(args.store)
    auth = AuthConfig.from_environment()

    try:
        if args.command == "auth":
            auth = AuthConfig.from_environment(compose=args.compose)
            authorize(auth)
            if args.compose:
                print("   this token can send. Only `send` does, and only with a "
                      "confirmation from `review`.")
            print(f"✓  authorised; token cached at {auth.token_path}")
            return 0

        if args.command == "status":
            report = status(store_path)
            print(f"   {report.path}")
            print(f"   {report.messages} message(s) in {report.threads} thread(s)")
            print(f"   newest: {report.newest or '(none)'}")
            print(f"   watermark: {report.watermark or '(none — next sync will be full)'}")
            if not has_token(auth):
                print("   not authorised yet — run: ./mail auth")
            return 0

        if args.command == "sync":
            label = None if args.label.lower() == "all" else args.label
            print(f"→  syncing {args.label}")
            report = sync_mailbox(auth, store_path, label=label, limit=args.limit,
                                  progress=lambda m: print(m))
            if report.recovered_gap:
                print("   the watermark had expired; recovered with a full resync")
            print(f"✓  {report.mode}: {report.added} added, {report.updated} updated, "
                  f"{report.deleted} deleted, {report.relabelled} relabelled")
            print(f"   {report.messages_total} message(s) stored")
            return 0

        if args.command == "threads":
            for thread in list_threads(store_path, limit=args.limit):
                print(f"   {thread.last}  {thread.messages:>2}  {thread.subject[:60]}")
                print(f"       {thread.thread_id}  {thread.started_by[:60]}")
            return 0

        if args.command == "read":
            detail = read_thread(store_path, args.thread_id)
            print(f"# {detail.subject}\n")
            for message in detail.messages:
                print(f"--- {message['from']}  {message['at']}")
                print(message["body"] or "(no text)")
                print()
            return 0

        if args.command == "search":
            for row in search_mail(store_path, args.text, limit=args.limit):
                print(f"   {row['at']}  {row['subject'][:50]}")
                print(f"       {row['thread_id']}  {row['from_addr'][:50]}")
            return 0

        # Everything past here needs a drafting token, which lives in a different file.
        compose_auth = AuthConfig.from_environment(compose=True)

        if args.command == "draft":
            report = draft_new(compose_auth, args.to, args.subject, args.body, cc=args.cc)
            print(f"draft {report.draft_id} to {report.to}")
            print(f"   review it:  ./mail review {report.draft_id}")
            return 0

        if args.command == "reply":
            report = draft_reply(compose_auth, store_path, args.thread_id, args.body)
            print(f"draft {report.draft_id} replying to {report.to}")
            print(f"   review it:  ./mail review {report.draft_id}")
            return 0

        if args.command == "drafts":
            for row in list_drafts(compose_auth):
                print(f"   {row['draft_id']}  thread {row['thread_id']}")
            return 0

        if args.command == "review":
            report = review_draft(compose_auth, args.draft_id)
            print(report.rendered)
            print()
            print(f"   send it:  ./mail send {report.draft_id} --confirm {report.confirmation}")
            return 0

        if args.command == "send":
            report = send_draft(compose_auth, args.draft_id, args.confirm)
            print(f"sent. message {report.message_id} in thread {report.thread_id}")
            return 0

        if args.command == "discard":
            discard_draft(compose_auth, args.draft_id)
            print(f"discarded {args.draft_id}")
            return 0

    except NotAuthorised as error:
        print(f"!  {error}", file=sys.stderr)
        return 1
    except SendRefused as error:
        # Not a crash: the gate working. Given its own exit code so a caller can tell a
        # refusal from a fault.
        print(f"!  not sent: {error}", file=sys.stderr)
        return 2
    except NeedsComposeScope as error:
        print(f"!  {error}", file=sys.stderr)
        return 1
    except MissingCredentials as error:
        print(f"!  {error}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"!  {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
