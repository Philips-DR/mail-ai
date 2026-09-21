"""Tests for the sync.

A mailbox is a log, not a folder, and these are the assertions that keep it one: that a
resync duplicates nothing, that an expired watermark recovers instead of failing, and that
the watermark is taken before the fetch rather than after.
"""

import pytest

from mailai.store import get_state, message_count, open_store, thread_messages
from mailai.sync import FULL_SYNC_DONE, HISTORY_KEY, is_gap, sync, to_row
from tests.fake_gmail import FakeGmail, HttpErrorStub, message


@pytest.fixture
def connection(tmp_path):
    return open_store(tmp_path / "mailbox.sqlite3")


@pytest.fixture
def gmail():
    return FakeGmail([
        message("m1", "t1", "Claims review", "Can we move it to Tuesday?", date=1000),
        message("m2", "t1", "Re: Claims review", "Tuesday works.", date=2000),
        message("m3", "t2", "Invoice", "Attached.", date=3000),
    ], history_id="500")


def test_a_first_sync_fetches_everything(gmail, connection):
    result = sync(gmail, connection, label="INBOX")
    assert result.mode == "full"
    assert result.added == 3
    assert message_count(connection) == 3


def test_paging_is_exhausted_rather_than_stopping_at_the_first_page(gmail, connection):
    """The fake pages two at a time; a sync that ignored list_next would find two of three."""
    sync(gmail, connection, label="INBOX")
    assert message_count(connection) == 3


def test_the_watermark_is_recorded_so_the_next_sync_is_incremental(gmail, connection):
    sync(gmail, connection, label="INBOX")
    assert get_state(connection, HISTORY_KEY) == "500"
    assert get_state(connection, FULL_SYNC_DONE) == "1"


def test_syncing_twice_changes_nothing_and_refetches_nothing(gmail, connection):
    sync(gmail, connection, label="INBOX")
    fetched_after_first = len(gmail.fetched)
    second = sync(gmail, connection, label="INBOX")

    assert second.mode == "incremental"
    assert message_count(connection) == 3
    assert len(gmail.fetched) == fetched_after_first  # nothing refetched


def test_a_new_message_arrives_through_history_not_a_rescan(gmail, connection):
    sync(gmail, connection, label="INBOX")
    gmail.mailbox["m4"] = message("m4", "t3", "New", "Just in", date=4000)
    gmail.history_records = [{"messagesAdded": [{"message": {"id": "m4"}}]}]
    gmail.history_id = "600"

    result = sync(gmail, connection, label="INBOX")
    assert result.mode == "incremental"
    assert result.added == 1
    assert message_count(connection) == 4


def test_a_deletion_is_applied(gmail, connection):
    sync(gmail, connection, label="INBOX")
    gmail.history_records = [{"messagesDeleted": [{"message": {"id": "m3"}}]}]
    result = sync(gmail, connection, label="INBOX")
    assert result.deleted == 1
    assert message_count(connection) == 2


def test_a_label_change_does_not_refetch_the_message(gmail, connection):
    """Reading, archiving and starring are the common case by a wide margin, and none of
    them changes the message."""
    sync(gmail, connection, label="INBOX")
    before = len(gmail.fetched)
    gmail.history_records = [{"labelsRemoved": [{"message": {"id": "m1"}, "labelIds": ["UNREAD"]}]}]

    result = sync(gmail, connection, label="INBOX")
    assert result.relabelled == 1
    assert len(gmail.fetched) == before


def test_an_expired_watermark_recovers_with_a_full_resync(gmail, connection):
    """Gmail keeps history for about a week. Past that the watermark 404s and there is no
    way to learn what was missed -- so a full resync is the only correct answer, and for a
    mailbox synced less often than weekly it is the expected path, not a failure."""
    sync(gmail, connection, label="INBOX")
    gmail.expire_watermark = True
    gmail.mailbox["m9"] = message("m9", "t9", "Missed", "While you were away", date=9000)

    result = sync(gmail, connection, label="INBOX")
    assert result.recovered_gap is True
    assert result.mode == "full"
    assert message_count(connection) == 4


def test_an_error_that_is_not_a_gap_is_not_swallowed(gmail, connection, monkeypatch):
    """Recovering from every failure by refetching the whole mailbox would hide real bugs
    behind a very expensive retry."""
    sync(gmail, connection, label="INBOX")

    def explode(*_a, **_k):
        raise HttpErrorStub(500)

    monkeypatch.setattr("mailai.client.list_history", explode)

    with pytest.raises(HttpErrorStub):
        sync(gmail, connection, label="INBOX")


def test_only_a_404_counts_as_a_gap():
    assert is_gap(HttpErrorStub(404)) is True
    assert is_gap(HttpErrorStub(500)) is False
    assert is_gap(ValueError("nothing to do with http")) is False


def test_a_limit_stops_a_first_sync_early(gmail, connection):
    result = sync(gmail, connection, label="INBOX", limit=2)
    assert result.added == 2


# ---------------------------------------------------------------------------
# Row construction
# ---------------------------------------------------------------------------

def test_a_message_becomes_a_row_with_its_quotes_already_split(connection):
    raw = message("m1", "t1", "Re: Plan", "Agreed.\n\nOn Mon, X wrote:\n> the old plan")
    row = to_row(raw)
    assert row["body"] == "Agreed."
    assert "old plan" in row["quoted"]
    assert row["normalised_subject"] == "Plan"


def test_thread_messages_come_back_in_the_order_they_were_sent(gmail, connection):
    sync(gmail, connection, label="INBOX")
    ordered = thread_messages(connection, "t1")
    assert [m["id"] for m in ordered] == ["m1", "m2"]
