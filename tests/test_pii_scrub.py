from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from mail_ingestor.gmail.reader import GmailReaderService
from mail_ingestor.schemas import EmailMessage, Summary


def _seed_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")


def test_summarize_redacts_pii_before_llm_and_in_record(monkeypatch):
    _seed_token(monkeypatch)
    from mail_ingestor.flow import MailIngestorFlow

    captured: dict[str, EmailMessage] = {}

    def capture_build_task(agent, parsed):
        captured["parsed"] = parsed
        return MagicMock()

    monkeypatch.setattr("mail_ingestor.flow.build_summarize_task", capture_build_task)

    fake_result = MagicMock(pydantic=Summary(tl_dr="t", summary="s", category="c"))

    def fake_crew_ctor(*_args: object, **_kwargs: object) -> MagicMock:
        instance = MagicMock()
        instance.kickoff.return_value = fake_result
        return instance

    monkeypatch.setattr("mail_ingestor.flow.Crew", fake_crew_ctor)

    reader = MagicMock(spec=GmailReaderService)
    flow = MailIngestorFlow(reader=reader, model="c")
    parsed = EmailMessage(
        message_id="m1",
        sender="Alice <alice@example.com>",
        subject="ping bob@corp.com",
        body_text="reach me at carol@x.org",
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    record = flow.summarize(parsed)

    scrubbed = captured["parsed"]
    assert "alice@example.com" not in scrubbed.sender
    assert "bob@corp.com" not in scrubbed.subject
    assert "carol@x.org" not in scrubbed.body_text
    assert "[REDACTED_EMAIL]" in scrubbed.body_text
    # persisted record's subject is scrubbed; message id preserved
    assert "bob@corp.com" not in record.subject
    assert "[REDACTED_EMAIL]" in record.subject
    assert record.source_message_id == "m1"
