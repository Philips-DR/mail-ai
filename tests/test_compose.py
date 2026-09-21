"""Tests for building the message that gets sent."""

import base64
import email

from mailai.compose import Composition, build_raw, draft_body, reply_subject


def parsed(composition: Composition):
    return email.message_from_bytes(base64.urlsafe_b64decode(build_raw(composition)))


def test_a_plain_message_carries_its_recipients_and_body():
    message = parsed(Composition(to="a@x.com", cc="c@x.com", subject="Hi", body="Hello."))
    assert message["To"] == "a@x.com"
    assert message["Cc"] == "c@x.com"
    assert message["Subject"] == "Hi"
    assert message.get_payload(decode=True).decode().strip() == "Hello."


def test_a_reply_carries_the_headers_other_clients_thread_on():
    """Gmail's threadId threads correctly in Gmail's UI and nowhere else. Every other client
    threads on In-Reply-To and References, so a reply sent without them arrives as a new
    conversation for anyone not using Gmail."""
    message = parsed(Composition(
        to="a@x.com", subject="Re: Claims", body="Agreed.",
        in_reply_to="<abc123@mail.example.com>",
    ))
    assert message["In-Reply-To"] == "<abc123@mail.example.com>"
    assert message["References"] == "<abc123@mail.example.com>"


def test_an_explicit_references_chain_is_preferred_over_the_fallback():
    message = parsed(Composition(
        to="a@x.com", subject="Re: Claims", body="Agreed.",
        in_reply_to="<second@x>", references="<first@x> <second@x>",
    ))
    assert message["References"] == "<first@x> <second@x>"


def test_a_message_that_is_not_a_reply_has_no_threading_headers():
    message = parsed(Composition(to="a@x.com", subject="Hi", body="Hello."))
    assert message["In-Reply-To"] is None
    assert message["References"] is None


def test_reply_subjects_do_not_accrete():
    assert reply_subject("Claims") == "Re: Claims"
    assert reply_subject("Re: Claims") == "Re: Claims"
    assert reply_subject("RE: Claims") == "RE: Claims"


def test_a_draft_reads_back_into_the_composition_it_represents():
    """The gate fingerprints what comes back from Gmail, not what was sent to it, so this
    round trip is what the whole mechanism rests on."""
    original = Composition(to="a@x.com", subject="Hi", body="Hello.")
    resource = {"message": {"payload": {
        "mimeType": "text/plain",
        "headers": [{"name": "To", "value": "a@x.com"}, {"name": "Subject", "value": "Hi"}],
        "body": {"data": base64.urlsafe_b64encode(b"Hello.").decode().rstrip("=")},
    }}}
    assert draft_body(resource).fingerprint() == original.fingerprint()


def test_the_fingerprint_ignores_only_surrounding_whitespace():
    assert (Composition(to="a@x.com", subject="Hi", body="Body ").fingerprint()
            == Composition(to="a@x.com ", subject=" Hi", body="Body").fingerprint())
    assert (Composition(to="a@x.com", subject="Hi", body="Body").fingerprint()
            != Composition(to="a@x.com", subject="Hi", body="Bodyy").fingerprint())
