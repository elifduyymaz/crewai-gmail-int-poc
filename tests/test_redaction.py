from datetime import UTC, datetime

from mail_ingestor.redaction import EMAIL_PLACEHOLDER, redact, redact_email_message
from mail_ingestor.schemas import EmailMessage


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


def _email(**over) -> EmailMessage:
    base = {
        "message_id": "m1",
        "sender": "Alice <alice@example.com>",
        "subject": "ping bob@corp.com",
        "body_text": "reach me at carol@x.org for details",
        "received_at": datetime(2026, 1, 1, tzinfo=UTC),
        "labels": ["INBOX"],
    }
    base.update(over)
    return EmailMessage(**base)


def test_redact_email_message_masks_body_sender_subject():
    out = redact_email_message(_email())
    assert out.body_text == "reach me at [REDACTED_EMAIL] for details"
    assert out.sender == "Alice <[REDACTED_EMAIL]>"
    assert out.subject == "ping [REDACTED_EMAIL]"


def test_redact_email_message_preserves_non_pii_fields():
    out = redact_email_message(_email())
    assert out.message_id == "m1"
    assert out.labels == ["INBOX"]


def test_redact_email_message_returns_new_frozen_instance():
    original = _email()
    out = redact_email_message(original)
    assert out is not original
    assert isinstance(out, EmailMessage)
    # original untouched
    assert original.body_text == "reach me at carol@x.org for details"


def test_redact_email_message_noop_when_no_pii():
    clean = _email(sender="Alice", subject="Weekly update", body_text="all good")
    out = redact_email_message(clean)
    assert out.sender == "Alice"
    assert out.subject == "Weekly update"
    assert out.body_text == "all good"
