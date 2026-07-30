from mail_ingestor.redaction import EMAIL_PLACEHOLDER, redact


def test_placeholder_constant():
    assert EMAIL_PLACEHOLDER == "[REDACTED_EMAIL]"


def test_redacts_a_single_email():
    assert redact("contact alice@example.com please") == f"contact {EMAIL_PLACEHOLDER} please"


def test_redacts_multiple_emails():
    out = redact("from a@x.com to b@y.org")
    assert out == f"from {EMAIL_PLACEHOLDER} to {EMAIL_PLACEHOLDER}"
    assert "@" not in out


def test_text_without_email_is_unchanged():
    text = "no addresses here, just words and numbers 12345"
    assert redact(text) == text


def test_empty_string():
    assert redact("") == ""


def test_masks_complex_email_format():
    assert redact("user.name+tag@sub.domain.co.uk") == EMAIL_PLACEHOLDER


def test_preserves_surrounding_punctuation():
    assert redact("<alice@example.com>") == f"<{EMAIL_PLACEHOLDER}>"


def test_does_not_match_bare_at_sign():
    # "@handle" (no domain) and "3 @ 5" are not emails — must stay unchanged.
    text = "ping @handle about the 3 @ 5 ratio"
    assert redact(text) == text
