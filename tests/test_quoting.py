"""Tests for separating what someone wrote from what their client quoted back.

Every rule here is a convention rather than a standard, so the tests that matter most are
the conservative ones: text that merely looks like a marker must survive.
"""

from mailai.quoting import normalise_subject, split_reply, strip_quoted


def test_a_standard_gmail_reply_keeps_only_the_new_part():
    text = (
        "Thanks, that works.\n"
        "\n"
        "On Mon, 1 Sep 2026 at 10:04, Kwame <k@x.com> wrote:\n"
        "> Can we move the review?\n"
        "> Let me know.\n"
    )
    assert strip_quoted(text) == "Thanks, that works."


def test_the_quoted_part_is_kept_rather_than_discarded():
    """It is how a mistake in reconstruction can still be checked. Throwing it away would
    make that unfalsifiable."""
    body, quoted = split_reply("New.\n\nOn Mon, X wrote:\n> old")
    assert body == "New."
    assert "old" in quoted


def test_a_signature_is_cut_at_the_rfc_delimiter():
    assert strip_quoted("The message.\n\n-- \nPhilip\nAyaData") == "The message."


def test_a_signature_delimiter_without_its_trailing_space_still_counts():
    """Many clients drop the space, though RFC 3676 specifies it."""
    assert strip_quoted("The message.\n\n--\nPhilip") == "The message."


def test_an_em_dash_in_prose_is_not_a_signature():
    """The delimiter is a whole line. Anything looser truncates real sentences."""
    text = "We agreed -- finally -- to ship it."
    assert strip_quoted(text) == text


def test_an_outlook_original_message_divider_cuts():
    text = "My reply.\n\n-----Original Message-----\nFrom: someone\nOld content"
    assert strip_quoted(text) == "My reply."


def test_an_outlook_header_block_cuts():
    text = "My reply.\n\nFrom: Kwame\nSent: Monday\nTo: Philip\nSubject: Claims\n\nOld"
    assert strip_quoted(text) == "My reply."


def test_a_lone_from_line_in_prose_is_not_a_quote():
    """"From:" only means a quote when it opens a block of headers; on its own it is as
    likely to be someone typing about a sender."""
    text = "The bounce came back.\nFrom: what I can tell it was a typo in the address."
    assert strip_quoted(text) == text


def test_an_attribution_line_does_not_survive_as_a_dangling_sentence():
    body = strip_quoted("Sure.\n\nOn Tuesday, Kwame wrote:\n> the question")
    assert "wrote:" not in body
    assert body == "Sure."


def test_a_message_that_is_only_a_quote_leaves_an_empty_body():
    """Real: a forward with no comment added. Empty is the honest answer."""
    assert strip_quoted("On Mon, X wrote:\n> everything") == ""


def test_a_message_with_nothing_to_strip_is_untouched():
    text = "Just a plain note.\nWith two lines."
    assert strip_quoted(text) == text


def test_re_and_fwd_accretion_is_normalised_away():
    assert normalise_subject("Re: Fwd: RE: Claims review") == "Claims review"
    assert normalise_subject("RE[2]: Budget") == "Budget"


def test_a_subject_that_merely_starts_with_re_is_left_alone():
    assert normalise_subject("Rebuild the pipeline") == "Rebuild the pipeline"
