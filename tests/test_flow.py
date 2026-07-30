from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from mail_ingestor.flow import (
    IngestState,
    MailIngestorFlow,
    NativeAnthropicLLM,
    _content_as_text,
    _to_anthropic_messages,
    build_summarize_task,
    build_summarizer_agent,
)
from mail_ingestor.gmail.labels import LabelResolver
from mail_ingestor.gmail.reader import GmailReaderService
from mail_ingestor.schemas import EmailMessage, RawMessage, Summary, SummaryRecord


def _gmail_dict(subject: str = "Hi", body: str = "hello") -> dict:
    return {
        "id": "m1",
        "internalDate": "1700000000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "alice@example.com"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": subject},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
        },
    }


def _seed_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")


# ---------- _content_as_text ----------


def test_content_as_text_passes_through_string() -> None:
    assert _content_as_text("hello") == "hello"


def test_content_as_text_returns_empty_for_none() -> None:
    assert _content_as_text(None) == ""


def test_content_as_text_joins_text_blocks_and_drops_others() -> None:
    blocks = [
        {"type": "text", "text": "hello "},
        {"type": "image", "source": "..."},
        {"type": "text", "text": "world"},
    ]
    assert _content_as_text(blocks) == "hello world"


# ---------- _to_anthropic_messages ----------


def test_to_anthropic_messages_bare_string_becomes_single_user_turn() -> None:
    turns, system = _to_anthropic_messages("hello")
    assert turns == [{"role": "user", "content": "hello"}]
    assert system is None


def test_to_anthropic_messages_passes_through_user_and_assistant() -> None:
    turns, system = _to_anthropic_messages(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
    )
    assert turns == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert system is None


def test_to_anthropic_messages_extracts_system_to_top_level() -> None:
    turns, system = _to_anthropic_messages(
        [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hi"},
        ]
    )
    assert turns == [{"role": "user", "content": "hi"}]
    assert system == "You are helpful."


def test_to_anthropic_messages_joins_multiple_system_prompts() -> None:
    turns, system = _to_anthropic_messages(
        [
            {"role": "system", "content": "One."},
            {"role": "user", "content": "q"},
            {"role": "system", "content": "Two."},
        ]
    )
    assert turns == [{"role": "user", "content": "q"}]
    assert system == "One.\n\nTwo."


# ---------- NativeAnthropicLLM ----------


def test_native_llm_calls_client_with_default_params(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_token(monkeypatch)
    llm = NativeAnthropicLLM(model="claude-test")

    fake_block = MagicMock()
    fake_block.text = "hello"
    llm._client = MagicMock()
    llm._client.messages.create.return_value = MagicMock(content=[fake_block])

    result = llm.call("say hi")

    assert result == "hello"
    llm._client.messages.create.assert_called_once_with(
        model="claude-test",
        max_tokens=4096,
        messages=[{"role": "user", "content": "say hi"}],
    )


def test_native_llm_forwards_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_token(monkeypatch)
    llm = NativeAnthropicLLM(model="claude-test")
    llm._client = MagicMock()
    llm._client.messages.create.return_value = MagicMock(content=[])

    llm.call(
        [
            {"role": "system", "content": "act cool"},
            {"role": "user", "content": "hi"},
        ]
    )

    kwargs = llm._client.messages.create.call_args.kwargs
    assert kwargs["system"] == "act cool"
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


def test_native_llm_omits_system_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_token(monkeypatch)
    llm = NativeAnthropicLLM(model="claude-test")
    llm._client = MagicMock()
    llm._client.messages.create.return_value = MagicMock(content=[])

    llm.call("hi")

    assert "system" not in llm._client.messages.create.call_args.kwargs


def test_native_llm_concatenates_text_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_token(monkeypatch)
    llm = NativeAnthropicLLM(model="claude-test")
    b1, b2 = MagicMock(), MagicMock()
    b1.text = "part-A "
    b2.text = "part-B"
    llm._client = MagicMock()
    llm._client.messages.create.return_value = MagicMock(content=[b1, b2])

    assert llm.call("hi") == "part-A part-B"


# ---------- IngestState ----------


def test_ingest_state_defaults() -> None:
    state = IngestState()
    assert state.message_id == ""
    assert state.raw is None
    assert state.parsed is None
    assert state.summary_record is None


# ---------- Agent / Task factories ----------


def test_build_summarizer_agent_has_expected_config(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_token(monkeypatch)
    reader = MagicMock(spec=GmailReaderService)
    resolver = MagicMock(spec=LabelResolver)

    agent = build_summarizer_agent(model="claude-x", reader=reader, resolver=resolver)

    assert agent.role == "Email Summarizer"
    assert agent.max_iter == 10
    assert agent.max_execution_time == 120
    assert agent.allow_delegation is False
    assert len(agent.tools) == 2


def test_build_summarize_task_uses_output_pydantic_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_token(monkeypatch)
    reader = MagicMock(spec=GmailReaderService)
    resolver = MagicMock(spec=LabelResolver)
    parsed = EmailMessage(
        message_id="m1",
        sender="a@x.com",
        received_at=datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
        subject="Test",
        body_text="hello",
    )
    agent = build_summarizer_agent(model="c", reader=reader, resolver=resolver)
    task = build_summarize_task(agent, parsed)

    assert task.output_pydantic is Summary
    assert task.agent is agent
    assert "Test" in task.description
    assert "a@x.com" in task.description


# ---------- MailIngestorFlow steps ----------


def test_flow_fetch_rejects_empty_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_token(monkeypatch)
    reader = MagicMock(spec=GmailReaderService)
    resolver = MagicMock(spec=LabelResolver)
    flow = MailIngestorFlow(reader=reader, resolver=resolver, model="c")
    # State.message_id defaults to "" — an unset kickoff must fail loud.
    with pytest.raises(ValueError, match="message_id"):
        flow.fetch()
    reader.get_message.assert_not_called()


def test_flow_fetch_wraps_gmail_dict_in_raw_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_token(monkeypatch)
    reader = MagicMock(spec=GmailReaderService)
    reader.get_message.return_value = {"id": "m1", "payload": {}}
    resolver = MagicMock(spec=LabelResolver)

    flow = MailIngestorFlow(reader=reader, resolver=resolver, model="c")
    flow.state.message_id = "m1"
    raw = flow.fetch()

    assert isinstance(raw, RawMessage)
    assert raw.message_id == "m1"
    assert raw.payload == {"id": "m1", "payload": {}}
    reader.get_message.assert_called_once_with("m1")


def test_flow_parse_produces_email_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_token(monkeypatch)
    reader = MagicMock(spec=GmailReaderService)
    resolver = MagicMock(spec=LabelResolver)

    flow = MailIngestorFlow(reader=reader, resolver=resolver, model="c")
    raw = RawMessage(message_id="m1", payload=_gmail_dict(subject="Hi", body="hello"))
    parsed = flow.parse(raw)

    assert isinstance(parsed, EmailMessage)
    assert parsed.sender == "alice@example.com"
    assert parsed.subject == "Hi"
    assert parsed.body_text.strip() == "hello"


# ---------- End-to-end kickoff (the Task 3.3 checkpoint) ----------


def test_kickoff_produces_valid_summary_record_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_token(monkeypatch)

    reader = MagicMock(spec=GmailReaderService)
    reader.get_message.return_value = _gmail_dict(subject="Weekly report", body="Sales up 20%")
    resolver = MagicMock(spec=LabelResolver)

    canned = json.dumps(
        {
            "tl_dr": "Weekly sales up 20%.",
            "summary": "Weekly report indicates 20% sales growth week over week.",
            "key_points": ["+20% sales"],
            "action_items": [],
            "category": "report",
        }
    )
    monkeypatch.setattr(
        "mail_ingestor.flow.NativeAnthropicLLM.call",
        lambda self, messages, **_kwargs: canned,
    )

    flow = MailIngestorFlow(reader=reader, resolver=resolver, model="claude-haiku-x")
    result = flow.kickoff(inputs={"message_id": "m1"})

    assert isinstance(result, SummaryRecord)
    assert result.source_message_id == "m1"
    assert result.subject == "Weekly report"
    assert result.model == "claude-haiku-x"
    assert result.summary.tl_dr == "Weekly sales up 20%."
    assert result.summary.category == "report"


def test_kickoff_env_opts_out_of_crewai_telemetry() -> None:
    import os

    import mail_ingestor.flow  # noqa: F401  — imports set the env var

    assert os.environ.get("CREWAI_TELEMETRY_OPT_OUT") == "1"
