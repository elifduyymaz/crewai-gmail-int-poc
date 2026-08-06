from datetime import UTC, datetime

from mail_ingestor.redaction import (
    EMAIL_PLACEHOLDER,
    redact,
    redact_email_message,
    redact_summary_record,
)
from mail_ingestor.schemas import EmailMessage, Summary, SummaryRecord


def test_placeholder_constant():
    # Task 5.5 AC #5: scrubbed emails render as ``[scrubbed]@example.com``
    # (a syntactically-valid placeholder that survives downstream email
    # regex validators).
    assert EMAIL_PLACEHOLDER == "[scrubbed]@example.com"


def test_redacts_a_single_email():
    assert redact("contact alice@example.com please") == f"contact {EMAIL_PLACEHOLDER} please"


def test_redacts_multiple_emails():
    out = redact("from a@x.com to b@y.org")
    assert out == f"from {EMAIL_PLACEHOLDER} to {EMAIL_PLACEHOLDER}"
    # ``@`` still present because the placeholder is a valid email shape;
    # what matters is neither original ``@x.com`` nor ``@y.org`` survives.
    assert "x.com" not in out
    assert "y.org" not in out


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
    assert out.body_text == "reach me at [scrubbed]@example.com for details"
    assert out.sender == "Alice <[scrubbed]@example.com>"
    assert out.subject == "ping [scrubbed]@example.com"


def test_redact_email_message_scrubs_recipients_and_snippet():
    # Defense in depth: EmailMessage carries several PII-shaped fields
    # beyond the summarizer prompt inputs. Recipients and snippet must
    # be scrubbed so a downstream module that consumes the parsed
    # message (logs, DLQ payload, telemetry) cannot leak raw addresses.
    out = redact_email_message(
        _email(
            recipients=["team@example.com", "cc-list@example.com"],
            snippet="preview: reach at cameron@example.com",
        )
    )
    assert out.recipients == ["[scrubbed]@example.com", "[scrubbed]@example.com"]
    assert out.snippet == "preview: reach at [scrubbed]@example.com"


def test_redact_email_message_leaves_attachment_filenames_alone():
    # Documented tradeoff: the stub email regex is greedy; scrubbing
    # ``invoice-alice@example.com.pdf`` would collapse to just
    # ``[scrubbed]@example.com`` and lose the ``.pdf`` suffix + the
    # ``invoice-`` prefix. Filenames rarely carry email-shaped PII in
    # normal usage; hand-inspect at demo commit time if a suspicious
    # attachment name appears in the source fixtures.
    out = redact_email_message(
        _email(
            attachment_filenames=["invoice-alice@example.com.pdf", "notes.txt"],
        )
    )
    assert out.attachment_filenames == ["invoice-alice@example.com.pdf", "notes.txt"]


def test_redact_email_message_preserves_non_pii_fields():
    out = redact_email_message(_email())
    assert out.message_id == "m1"
    assert out.labels == ["INBOX"]


def test_redact_email_message_handles_none_snippet() -> None:
    # ``EmailMessage.snippet`` is Optional[str]; a None must not crash the
    # scrubber (regex would raise on None input).
    out = redact_email_message(_email(snippet=None))
    assert out.snippet is None


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


# ─────────────────────────────────────────────────────────────
# redact_summary_record — Task 5.5 output-side scrubber
# ─────────────────────────────────────────────────────────────


def _summary_record(**over) -> SummaryRecord:
    base = {
        "source_message_id": "m1",
        "subject": "ping bob@corp.com about the report",
        "summary": Summary(
            tl_dr="alice@example.com asked about metrics",
            summary="Recap: alice@example.com wants the Q1 numbers by Friday.",
            key_points=["contact bob@corp.com", "deliver by Friday"],
            action_items=["email carol@x.org with the deck"],
            category="report",
        ),
        "model": "claude-haiku-4-5-20251001",
        "tokens_prompt": 500,
        "tokens_completion": 120,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    base.update(over)
    return SummaryRecord(**base)


def test_redact_summary_record_scrubs_subject_and_nested_summary_fields():
    out = redact_summary_record(_summary_record())
    assert "bob@corp.com" not in out.subject
    assert out.subject == f"ping {EMAIL_PLACEHOLDER} about the report"
    assert out.summary.tl_dr == f"{EMAIL_PLACEHOLDER} asked about metrics"
    assert (
        out.summary.summary
        == f"Recap: {EMAIL_PLACEHOLDER} wants the Q1 numbers by Friday."
    )
    assert out.summary.key_points == [
        f"contact {EMAIL_PLACEHOLDER}",
        "deliver by Friday",
    ]
    assert out.summary.action_items == [f"email {EMAIL_PLACEHOLDER} with the deck"]


def test_redact_summary_record_preserves_provenance_fields():
    original = _summary_record()
    out = redact_summary_record(original)
    assert out.source_message_id == original.source_message_id
    assert out.model == original.model
    assert out.tokens_prompt == original.tokens_prompt
    assert out.tokens_completion == original.tokens_completion
    assert out.created_at == original.created_at


def test_redact_summary_record_returns_new_frozen_instance():
    original = _summary_record()
    out = redact_summary_record(original)
    assert out is not original
    assert out.summary is not original.summary
    # original untouched — the frozen model was not mutated in place.
    assert "bob@corp.com" in original.subject
    assert "alice@example.com" in original.summary.tl_dr


def test_redact_summary_record_noop_when_no_pii():
    clean = _summary_record(
        subject="Weekly update",
        summary=Summary(
            tl_dr="Numbers up",
            summary="Sales grew 20% WoW.",
            key_points=["+20% sales"],
            action_items=[],
            category="report",
        ),
    )
    out = redact_summary_record(clean)
    assert out.subject == "Weekly update"
    assert out.summary.summary == "Sales grew 20% WoW."
