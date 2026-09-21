"""mail-ai's second front door: the same operations the CLI exposes, reachable by a model.

Both doors sit directly on operations.py; neither wraps the other.

Every tool here is either read-only or writes to the local mailbox file, and nothing can
reach the outside world: the OAuth scope is gmail.readonly, so this door is structurally
incapable of sending, replying or deleting anything in Gmail. That is why it can be opened
before the send gate exists rather than after -- the capability simply is not there to
misuse. When drafting and sending arrive, they arrive with the gate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from mailai.auth import AuthConfig
from mailai.operations import (
    as_dict,
    list_threads,
    read_thread,
    search_mail,
    status,
    sync_mailbox,
)

VERSION = "0.1.0"


@dataclass(frozen=True)
class ServerPaths:
    """Where this server may read and write. Supplied by the caller, never assumed."""

    store: Path


def _failed(error: Exception) -> dict:
    return {"error": str(error)}


def create_mail_ai_server(paths: ServerPaths, auth: AuthConfig) -> MCPServer:
    server = MCPServer(name="mail-ai", version=VERSION)

    @server.tool(
        name="status",
        title="What the local mailbox holds",
        description=(
            "How many messages and threads are stored, how recent they are, and whether a "
            "sync watermark exists. Reads the local file only — no network, no "
            "credentials. The safe thing to check first."
        ),
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def _status() -> dict:
        try:
            return as_dict(status(paths.store))
        except (OSError, ValueError) as error:
            return _failed(error)

    @server.tool(
        name="sync",
        title="Bring the local mailbox level with Gmail",
        description=(
            "Fetch new and changed mail into the local store. Incremental after the first "
            "run; falls back to a full resync by itself if the watermark has expired, "
            "which happens after about a week. Writes only to the local mailbox file and "
            "changes nothing in Gmail."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    def _sync(label: str = "INBOX", limit: int | None = None) -> dict:
        try:
            target = None if label.lower() == "all" else label
            return as_dict(sync_mailbox(auth, paths.store, label=target, limit=limit))
        except Exception as error:
            return _failed(error)

    @server.tool(
        name="list_threads",
        title="Recent threads",
        description=(
            "The most recent conversations, newest first, with who started each and how "
            "many messages it holds. Offline: reads only what a previous sync stored."
        ),
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def _list_threads(limit: int = 20) -> dict:
        try:
            return {"threads": [as_dict(t) for t in list_threads(paths.store, limit=limit)]}
        except (OSError, ValueError) as error:
            return _failed(error)

    @server.tool(
        name="read_thread",
        title="Read one thread",
        description=(
            "Every message in a thread, oldest first, with each one's quoted history "
            "already removed — so a ten-message thread reads as ten messages rather "
            "than ten copies of the first. Offline."
        ),
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def _read_thread(thread_id: str) -> dict:
        try:
            return as_dict(read_thread(paths.store, thread_id))
        except (OSError, ValueError) as error:
            return _failed(error)

    @server.tool(
        name="search",
        title="Search the local mailbox",
        description=(
            "Find messages whose subject, body or sender contains the given text. Offline, "
            "over what a previous sync stored — not a Gmail search."
        ),
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    def _search(text: str, limit: int = 20) -> dict:
        try:
            return {"messages": search_mail(paths.store, text, limit=limit)}
        except (OSError, ValueError) as error:
            return _failed(error)

    return server


def main() -> None:
    """The one place this entry point reaches for the environment, named so it is visible."""
    root = Path(__file__).resolve().parent.parent
    paths = ServerPaths(store=Path(os.environ.get("MAIL_AI_STORE", root / "mailbox.sqlite3")))
    create_mail_ai_server(paths, AuthConfig.from_environment()).run(transport="stdio")


if __name__ == "__main__":
    main()
