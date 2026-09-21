"""Tests for the send gate.

This is the only part of the suite where being wrong puts words in someone else's inbox, so
the tests are about what must NOT happen: sending without review, sending something that
changed after review, and any path that composes and sends in one step.
"""

import inspect

import pytest

from mailai import drafts, sending
from mailai.compose import Composition
from mailai.sending import DraftChanged, NotReviewed, review, send
from tests.fake_drafts import FakeDrafts


@pytest.fixture
def gmail():
    service = FakeDrafts()
    service.add("kwame@example.com", "Claims review", "Tuesday works for me.")
    return service


def test_send_takes_no_message_content_at_all():
    """The structural half of the gate, and the reason it cannot be forgotten. If a `body`
    or `to` parameter ever appears here, compose-and-send becomes possible in one call and
    every other guarantee in this module is decoration."""
    parameters = set(inspect.signature(send).parameters)
    assert parameters == {"service", "draft_id", "confirmation"}


def test_no_function_in_the_sending_module_accepts_a_body():
    forbidden = {"body", "to", "subject", "raw", "message", "content"}
    for name, function in inspect.getmembers(sending, inspect.isfunction):
        if function.__module__ != sending.__name__:
            continue
        assert not (set(inspect.signature(function).parameters) & forbidden), name


def test_a_reviewed_draft_sends(gmail):
    reviewed = review(gmail, "d1")
    send(gmail, "d1", reviewed.confirmation)
    assert gmail.sent == ["d1"]


def test_sending_without_a_confirmation_is_refused(gmail):
    with pytest.raises(NotReviewed):
        send(gmail, "d1", "")
    assert gmail.sent == []


def test_sending_with_a_confirmation_for_different_content_is_refused(gmail):
    with pytest.raises(DraftChanged):
        send(gmail, "d1", "0000000000000000")
    assert gmail.sent == []


def test_a_draft_edited_after_review_is_refused(gmail):
    """The reason the gate fingerprints content rather than trusting the draft id: the id
    does not change when the draft does, so an id alone would authorise sending something
    nobody read."""
    reviewed = review(gmail, "d1")
    gmail.edit("d1", "someone-else@example.com", "Claims review", "Transfer the funds.")

    with pytest.raises(DraftChanged):
        send(gmail, "d1", reviewed.confirmation)
    assert gmail.sent == []


def test_re_reviewing_after_an_edit_allows_the_new_version(gmail):
    """The gate refuses stale approval; it does not refuse forever."""
    review(gmail, "d1")
    gmail.edit("d1", "kwame@example.com", "Claims review", "Wednesday, actually.")
    send(gmail, "d1", review(gmail, "d1").confirmation)
    assert gmail.sent == ["d1"]


def test_adding_a_bcc_after_review_invalidates_the_confirmation():
    """Silently adding a recipient is exactly the edit this exists to catch, and bcc is the
    one a rendered review is least likely to make obvious."""
    before = Composition(to="a@x.com", subject="Hi", body="Body")
    after = Composition(to="a@x.com", subject="Hi", body="Body", bcc="quiet@x.com")
    assert before.fingerprint() != after.fingerprint()


def test_whitespace_around_a_confirmation_is_tolerated(gmail):
    """A token copied from terminal output should not fail on a trailing space."""
    reviewed = review(gmail, "d1")
    send(gmail, "d1", f"  {reviewed.confirmation}  ")
    assert gmail.sent == ["d1"]


def test_a_review_renders_every_recipient_it_fingerprints(gmail):
    """Approving what you cannot see is not approval."""
    gmail.store["d1"]["message"]["payload"]["headers"].append({"name": "Cc", "value": "c@x.com"})
    rendered = review(gmail, "d1").rendered()
    assert "kwame@example.com" in rendered
    assert "c@x.com" in rendered
    assert "Claims review" in rendered


def test_drafting_never_sends(gmail):
    """The whole point of the module split: nothing in drafts.py puts mail in front of
    anyone."""
    drafts.create(gmail, Composition(to="a@x.com", subject="Hi", body="Body"))
    drafts.delete(gmail, "d1")
    assert gmail.sent == []
