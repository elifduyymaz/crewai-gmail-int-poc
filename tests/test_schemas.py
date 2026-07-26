from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mail_ingestor.schemas import (
    DeadLetterRecord,
    EmailMessage,
    ProcessingStage,
    Summary,
    SummaryRecord,
)

AWARE = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


def _email(**overrides):
    base = {"message_id": "m1", "sender": "a@example.com", "received_at": AWARE}
    base.update(overrides)
    return EmailMessage(**base)


def _summary(**overrides):
    base = {"tl_dr": "t", "summary": "s", "category": "report"}
    base.update(overrides)
    return Summary(**base)


# --- EmailMessage + base behaviors ---


def test_email_message_minimal_valid():
    msg = _email()
    assert msg.message_id == "m1"
    assert msg.subject == ""
    assert msg.recipients == []
    assert msg.body_text == ""


def test_email_message_full_valid():
    msg = _email(
        thread_id="t1",
        subject="Hi",
        recipients=["b@example.com"],
        labels=["poc/reports"],
        body_text="hello",
        snippet="he...",
        attachment_filenames=["a.pdf"],
    )
    assert msg.labels == ["poc/reports"]
    assert msg.attachment_filenames == ["a.pdf"]


def test_email_message_requires_message_id_nonempty():
    with pytest.raises(ValidationError):
        _email(message_id="")


def test_email_message_requires_sender():
    with pytest.raises(ValidationError):
        EmailMessage(message_id="m1", received_at=AWARE)


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        _email(unexpected="x")


def test_model_is_frozen():
    msg = _email()
    with pytest.raises((ValidationError, TypeError)):
        msg.subject = "changed"


def test_naive_datetime_rejected():
    with pytest.raises(ValidationError):
        _email(received_at=datetime(2026, 7, 24, 12, 0))  # noqa: DTZ001 (intentionally naive)


def test_email_message_json_round_trip():
    msg = _email(subject="Hi", body_text="hello")
    assert EmailMessage.model_validate_json(msg.model_dump_json()) == msg


# --- Summary ---


def test_summary_valid_and_defaults():
    s = _summary()
    assert s.key_points == []
    assert s.action_items == []


def test_summary_requires_nonempty_text_fields():
    with pytest.raises(ValidationError):
        _summary(tl_dr="")
    with pytest.raises(ValidationError):
        _summary(summary="")
    with pytest.raises(ValidationError):
        _summary(category="")


# --- SummaryRecord ---


def test_summary_record_nests_summary_and_defaults_created_at():
    rec = SummaryRecord(
        source_message_id="m1", subject="Hi", summary=_summary(), model="claude-sonnet"
    )
    assert isinstance(rec.summary, Summary)
    assert rec.created_at.tzinfo is not None


def test_summary_record_accepts_dict_for_nested_summary():
    rec = SummaryRecord(
        source_message_id="m1",
        summary={"tl_dr": "t", "summary": "s", "category": "c"},
        model="m",
    )
    assert rec.summary.tl_dr == "t"


def test_summary_record_requires_nonempty_fields():
    with pytest.raises(ValidationError):
        SummaryRecord(source_message_id="", summary=_summary(), model="m")
    with pytest.raises(ValidationError):
        SummaryRecord(source_message_id="m1", summary=_summary(), model="")


def test_summary_record_json_round_trip():
    rec = SummaryRecord(source_message_id="m1", summary=_summary(), model="m")
    assert SummaryRecord.model_validate_json(rec.model_dump_json()) == rec


def test_summary_record_no_protected_namespace_warning(recwarn):
    SummaryRecord(source_message_id="m1", summary=_summary(), model="m")
    assert not [w for w in recwarn.list if "protected namespace" in str(w.message).lower()]


# --- ProcessingStage + DeadLetterRecord ---


def test_processing_stage_values():
    assert {s.value for s in ProcessingStage} == {"read", "parse", "summarize", "persist"}


def test_dead_letter_valid_with_enum():
    d = DeadLetterRecord(stage=ProcessingStage.PARSE, error="boom")
    assert d.stage is ProcessingStage.PARSE
    assert d.source_message_id is None
    assert d.failed_at.tzinfo is not None


def test_dead_letter_accepts_stage_string():
    d = DeadLetterRecord(stage="summarize", error="x")
    assert d.stage is ProcessingStage.SUMMARIZE


def test_dead_letter_rejects_unknown_stage():
    with pytest.raises(ValidationError):
        DeadLetterRecord(stage="unknown", error="x")


def test_dead_letter_requires_nonempty_error():
    with pytest.raises(ValidationError):
        DeadLetterRecord(stage=ProcessingStage.READ, error="")


def test_dead_letter_json_round_trip():
    d = DeadLetterRecord(source_message_id="m1", stage=ProcessingStage.PERSIST, error="db down")
    assert DeadLetterRecord.model_validate_json(d.model_dump_json()) == d


# --- Additional boundary coverage (final-review nits) ---


def test_email_message_requires_received_at():
    with pytest.raises(ValidationError):
        EmailMessage(message_id="m1", sender="a@example.com")


def test_summary_record_rejects_naive_created_at():
    with pytest.raises(ValidationError):
        SummaryRecord(
            source_message_id="m1",
            summary=_summary(),
            model="m",
            created_at=datetime(2026, 7, 24, 12, 0),  # noqa: DTZ001
        )


def test_dead_letter_rejects_naive_failed_at():
    with pytest.raises(ValidationError):
        DeadLetterRecord(
            stage=ProcessingStage.READ,
            error="x",
            failed_at=datetime(2026, 7, 24, 12, 0),  # noqa: DTZ001
        )
