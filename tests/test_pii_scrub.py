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
    assert "[scrubbed]@example.com" in scrubbed.body_text
    # persisted record's subject is scrubbed; message id preserved
    assert "bob@corp.com" not in record.subject
    assert "[scrubbed]@example.com" in record.subject
    assert record.source_message_id == "m1"


def test_input_side_scrub_survives_full_batch_end_to_end(monkeypatch, tmp_path, capsys):
    """End-to-end integration: fixture with real email addresses in the
    body goes through main._run_batch (real parser + real MailIngestorFlow
    with mocked LLM). Assert the stdout JSON — the artifact operators see
    — carries no raw email addresses.

    Complements the mid-level ``test_summarize_redacts_pii_before_llm_and_in_record``
    which mocks Crew + build_summarize_task. This test only mocks the
    outer edges (Gmail, LLM adapter) so the real flow + real redact +
    real vault + real stdout path is exercised.
    """
    import json
    import sqlite3

    from mail_ingestor import main as main_mod
    from mail_ingestor.flow import NativeAnthropicLLM
    from mail_ingestor.persistence.db import bootstrap

    _seed_token(monkeypatch)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)

    # Fixture payload: a Gmail resource dict with email addresses in
    # every content field the summarizer prompt could see.
    import base64

    body_plain = "Reply to alice@internal.example or ping bob@internal.example soon."
    fixture_payload = {
        "id": "leak-test-1",
        "internalDate": "1700000000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "sarah.jones@company.example"},
                {"name": "To", "value": "team@company.example"},
                {"name": "Subject", "value": "Q4 review — ping carol@company.example"},
            ],
            "body": {
                "data": base64.urlsafe_b64encode(body_plain.encode()).decode(),
            },
        },
    }

    fake_gmail = MagicMock(name="gmail_client")
    monkeypatch.setattr(main_mod, "_build_gmail_client", lambda: fake_gmail)

    fake_resolver_cls = MagicMock(name="LabelResolver")
    fake_resolver_cls.return_value.resolve.return_value = "label-x"
    monkeypatch.setattr("mail_ingestor.gmail.labels.LabelResolver", fake_resolver_cls)

    fake_reader_cls = MagicMock(name="GmailReaderService")
    fake_reader = fake_reader_cls.return_value
    fake_reader.list_message_ids.return_value = [fixture_payload["id"]]
    fake_reader.get_message.side_effect = lambda mid: fixture_payload
    monkeypatch.setattr("mail_ingestor.gmail.reader.GmailReaderService", fake_reader_cls)

    real_conn = sqlite3.connect(":memory:")
    real_conn.row_factory = sqlite3.Row
    bootstrap(real_conn)
    monkeypatch.setattr("mail_ingestor.persistence.db.init_db", lambda _p: real_conn)

    canned = json.dumps(
        {
            "tl_dr": "quarterly review touched several stakeholders",
            "summary": "Team was asked to reply — no addresses reproduced here.",
            "key_points": [],
            "action_items": [],
            "category": "report",
        }
    )
    monkeypatch.setattr(NativeAnthropicLLM, "call", lambda self, *a, **k: canned)

    rc = main_mod.main(["--label", "L", "--limit", "1"])
    assert rc == 0

    stdout = capsys.readouterr().out
    # The stdout JSON must not carry any of the raw addresses that lived
    # in the fixture's subject / sender / body. If PII scrub is bypassed
    # (regression in flow.summarize), any of these appears here.
    for raw in [
        "sarah.jones@company.example",
        "team@company.example",
        "carol@company.example",
        "alice@internal.example",
        "bob@internal.example",
    ]:
        assert raw not in stdout, (
            f"NFR-S3 breach: raw address {raw!r} survived to stdout JSON"
        )
