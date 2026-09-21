"""Tests for the MCP front door.

Driven through list_tools() and call_tool(), which is the same path a real session takes
once the transport is stripped away. No network, no credentials, no Gmail.
"""

import asyncio
import contextlib
import io
import json

import pytest

from mailai.auth import AuthConfig
from mailai.mcp_server import ServerPaths, create_mail_ai_server
from mailai.store import open_store, upsert_message


@pytest.fixture
def store_path(tmp_path):
    path = tmp_path / "mailbox.sqlite3"
    connection = open_store(path)
    for n, (mid, thread, body) in enumerate([
        ("m1", "t1", "Can we move the review?"),
        ("m2", "t1", "Tuesday works."),
        ("m3", "t2", "Invoice attached."),
    ]):
        upsert_message(connection, dict(
            id=mid, thread_id=thread, internal_date=1000 + n, from_addr="a@x.com",
            to_addr="me@x.com", cc_addr="", subject="Claims review",
            normalised_subject="Claims review", snippet=body[:20], body=body, quoted="",
            labels=["INBOX"], attachments=[],
        ))
    connection.commit()
    return path


@pytest.fixture
def server(store_path, tmp_path):
    return create_mail_ai_server(
        ServerPaths(store=store_path),
        AuthConfig(credentials_path=tmp_path / "nope.json", token_path=tmp_path / "nope-token.json"),
    )


def call(server, name, **arguments) -> dict:
    result = asyncio.run(server.call_tool(name, arguments))
    text = "".join(b.text for b in result.content if b.type == "text")
    return json.loads(text)


def test_the_door_advertises_exactly_the_read_path_and_sync(server):
    names = sorted(t.name for t in asyncio.run(server.list_tools()))
    assert names == ["list_threads", "read_thread", "search", "status", "sync"]


def test_nothing_here_can_send_reply_or_delete(server):
    """The door opened before the send gate exists, which is only safe because the
    capability is absent rather than merely ungranted. If a sending tool ever appears here
    without a gate, this test is the thing that should have stopped it."""
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert not names & {"send", "reply", "draft", "delete", "trash", "archive"}


def test_only_sync_is_marked_as_writing(server):
    read_only = {t.name: t.annotations.read_only_hint for t in asyncio.run(server.list_tools())}
    assert read_only["sync"] is False
    assert all(read_only[n] is True for n in ("status", "list_threads", "read_thread", "search"))


def test_status_works_offline_with_no_credentials(server):
    report = call(server, "status")
    assert report["messages"] == 3
    assert report["threads"] == 2
    assert report["full_sync_complete"] is False


def test_threads_come_back_newest_first(server):
    threads = call(server, "list_threads")["threads"]
    assert [t["thread_id"] for t in threads] == ["t2", "t1"]


def test_a_thread_reads_oldest_first(server):
    """A conversation is read in the order it happened, whatever order threads are listed."""
    detail = call(server, "read_thread", thread_id="t1")
    assert [m["id"] for m in detail["messages"]] == ["m1", "m2"]


def test_an_unknown_thread_is_an_error_not_an_exception(server):
    """A door that raises takes the session down with it."""
    assert "error" in call(server, "read_thread", thread_id="nope")


def test_search_finds_by_body_text(server):
    found = call(server, "search", text="Tuesday")["messages"]
    assert [m["id"] for m in found] == ["m2"]


def test_nothing_is_written_to_stdout_while_a_tool_runs(server):
    """On stdio, stdout IS the protocol stream: one stray line corrupts the session."""
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        call(server, "status")
        call(server, "list_threads")
        call(server, "search", text="review")
    assert sink.getvalue() == ""


def test_a_sync_without_credentials_reports_rather_than_crashing(server):
    """The one tool that needs the network must fail like every other tool here."""
    assert "error" in call(server, "sync", limit=1)
