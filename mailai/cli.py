"""./mail -- the human front door."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mailai.auth import AuthConfig, MissingCredentials, authorize, has_token
from mailai.operations import list_threads, read_thread, search_mail, status, sync_mailbox

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STORE = Path(os.environ.get("MAIL_AI_STORE", ROOT / "mailbox.sqlite3"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="A correct Gmail client. Read-only, for now.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-s", "--store", default=str(DEFAULT_STORE), help="The local mailbox")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="Authorise against Gmail and cache a refresh token")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store_path = Path(args.store)
    auth = AuthConfig.from_environment()

    try:
        if args.command == "auth":
            authorize(auth)
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

    except MissingCredentials as error:
        print(f"!  {error}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"!  {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
