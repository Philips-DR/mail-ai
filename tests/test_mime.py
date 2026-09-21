"""Tests for turning a Gmail payload into text."""

import base64

from mailai.mime import attachments, body_text, decode_body, headers, html_to_text


def b64(text: str, pad: bool = False) -> str:
    encoded = base64.urlsafe_b64encode(text.encode()).decode()
    return encoded if pad else encoded.rstrip("=")


def test_unpadded_base64url_decodes():
    """Gmail strips the "=" padding and the standard decoder rejects it. Without restoring
    it, any body whose length is not a multiple of four reads as empty."""
    assert decode_body(b64("hello")) == "hello"
    assert decode_body(b64("hell")) == "hell"
    assert decode_body(b64("hel")) == "hel"


def test_padded_base64url_still_decodes():
    assert decode_body(b64("hello", pad=True)) == "hello"


def test_undecodable_data_is_empty_rather_than_an_exception():
    """One malformed message must not stop a sync of ten thousand."""
    assert decode_body("!!!not base64!!!") == ""


def test_headers_are_lowercased():
    """Gmail is inconsistent about header case between calls."""
    payload = {"headers": [{"name": "Subject", "value": "Hi"}, {"name": "FROM", "value": "a@b"}]}
    assert headers(payload) == {"subject": "Hi", "from": "a@b"}


def test_plain_text_is_preferred_over_html():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": b64("the plain one")}},
            {"mimeType": "text/html", "body": {"data": b64("<p>the html one</p>")}},
        ],
    }
    assert body_text(payload) == "the plain one"


def test_an_html_only_message_is_stripped_rather_than_skipped():
    """Plenty of real senders never attach a plain alternative; dropping them would leave
    silent holes in a mailbox."""
    payload = {"mimeType": "text/html", "body": {"data": b64("<p>Hello</p><p>There</p>")}}
    assert body_text(payload) == "Hello\n\nThere"


def test_text_nested_several_parts_deep_is_still_found():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "application/pdf", "filename": "x.pdf", "body": {"size": 10}},
            {"mimeType": "multipart/alternative",
             "parts": [{"mimeType": "text/plain", "body": {"data": b64("buried")}}]},
        ],
    }
    assert body_text(payload) == "buried"


def test_an_attachments_text_is_not_mistaken_for_the_body():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "filename": "notes.txt", "body": {"data": b64("attached")}},
            {"mimeType": "text/plain", "body": {"data": b64("the actual message")}},
        ],
    }
    assert body_text(payload) == "the actual message"


def test_attachments_are_listed_without_fetching_their_bytes():
    payload = {
        "parts": [
            {"mimeType": "application/pdf", "filename": "invoice.pdf",
             "body": {"size": 4096, "attachmentId": "abc"}},
            {"mimeType": "text/plain", "body": {"data": b64("body")}},
        ]
    }
    listed = attachments(payload)
    assert len(listed) == 1
    assert listed[0]["filename"] == "invoice.pdf"
    assert listed[0]["size"] == 4096


def test_gmails_own_quote_wrapper_is_removed_before_tags_are():
    """Otherwise the quoted history survives as unattributed prose once markup is gone."""
    html = '<p>New reply</p><div class="gmail_quote">On Mon X wrote: everything older</div>'
    assert html_to_text(html) == "New reply"


def test_script_and_style_never_reach_the_text():
    html = "<style>p{color:red}</style><p>Visible</p><script>alert(1)</script>"
    assert html_to_text(html).strip() == "Visible"


def test_entities_are_unescaped():
    assert html_to_text("<p>Tom &amp; Jerry</p>") == "Tom & Jerry"


def test_a_payload_with_no_text_at_all_is_empty_not_an_error():
    assert body_text({"mimeType": "image/png", "body": {"size": 100}}) == ""
