from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from mail_ingestor.config import MissingSettingError
from mail_ingestor.flow import (
    EmptyLLMResponse,
    IngestState,
    MailIngestorFlow,
    NativeAnthropicLLM,
    SummaryValidationError,
    TokenUsage,
    ToolUseNotSupported,
    _content_as_text,
    _make_token_step_callback,
    _to_anthropic_messages,
    _truncate,
    build_summarize_task,
    build_summarizer_agent,
)
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

    llm._client = MagicMock()
    llm._client.messages.create.return_value = _mock_text_response(text="hello")

    result = llm.call("say hi")

    assert result == "hello"
    llm._client.messages.create.assert_called_once_with(
        model="claude-test",
        max_tokens=4096,
        messages=[{"role": "user", "content": "say hi"}],
    )


def _mock_text_response(text: str = "ok", stop_reason: str = "end_turn") -> MagicMock:
    """Build a fake Anthropic Response with a single text block."""
    block = MagicMock()
    block.text = text
    block.type = "text"
    return MagicMock(content=[block], stop_reason=stop_reason)


def test_native_llm_forwards_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_token(monkeypatch)
    llm = NativeAnthropicLLM(model="claude-test")
    llm._client = MagicMock()
    llm._client.messages.create.return_value = _mock_text_response()

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
    llm._client.messages.create.return_value = _mock_text_response()

    llm.call("hi")

    assert "system" not in llm._client.messages.create.call_args.kwargs


def test_native_llm_raises_on_non_empty_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_token(monkeypatch)
    llm = NativeAnthropicLLM(model="claude-test")
    llm._client = MagicMock()

    with pytest.raises(ToolUseNotSupported, match="forwarding is not implemented"):
        llm.call("hi", tools=[{"name": "some_tool"}])
    llm._client.messages.create.assert_not_called()


def test_native_llm_accepts_none_and_empty_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_token(monkeypatch)
    llm = NativeAnthropicLLM(model="claude-test")
    llm._client = MagicMock()
    llm._client.messages.create.return_value = _mock_text_response()

    assert llm.call("hi", tools=None) == "ok"
    assert llm.call("hi", tools=[]) == "ok"


def test_native_llm_raises_on_empty_response_with_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_token(monkeypatch)
    llm = NativeAnthropicLLM(model="claude-test")
    llm._client = MagicMock()
    tool_use_block = MagicMock()
    del tool_use_block.text  # simulate a block that has no `text` attribute
    tool_use_block.type = "tool_use"
    llm._client.messages.create.return_value = MagicMock(
        content=[tool_use_block], stop_reason="tool_use"
    )

    with pytest.raises(EmptyLLMResponse, match="stop_reason='tool_use'"):
        llm.call("hi")


def test_native_llm_raises_on_truncated_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_token(monkeypatch)
    llm = NativeAnthropicLLM(model="claude-test")
    llm._client = MagicMock()
    llm._client.messages.create.return_value = MagicMock(content=[], stop_reason="max_tokens")

    with pytest.raises(EmptyLLMResponse, match="stop_reason='max_tokens'"):
        llm.call("hi")


def test_native_llm_concatenates_text_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_token(monkeypatch)
    llm = NativeAnthropicLLM(model="claude-test")
    b1, b2 = MagicMock(), MagicMock()
    b1.text = "part-A "
    b2.text = "part-B"
    llm._client = MagicMock()
    llm._client.messages.create.return_value = MagicMock(content=[b1, b2], stop_reason="end_turn")

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

    agent = build_summarizer_agent(model="claude-x")

    assert agent.role == "Email Summarizer"
    assert agent.max_iter == 10
    assert agent.max_execution_time == 120
    assert agent.allow_delegation is False
    # Tools are deliberately not declared until forwarding is implemented in
    # NativeAnthropicLLM — see build_summarizer_agent's docstring.
    assert agent.tools == []


def test_build_summarize_task_uses_output_pydantic_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_token(monkeypatch)
    parsed = EmailMessage(
        message_id="m1",
        sender="a@x.com",
        received_at=datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
        subject="Test",
        body_text="hello",
    )
    agent = build_summarizer_agent(model="c")
    task = build_summarize_task(agent, parsed)

    assert task.output_pydantic is Summary
    assert task.agent is agent
    assert "Test" in task.description
    assert "a@x.com" in task.description


# ---------- MailIngestorFlow steps ----------


def test_flow_fetch_rejects_empty_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_token(monkeypatch)
    reader = MagicMock(spec=GmailReaderService)
    flow = MailIngestorFlow(reader=reader, model="c")
    # State.message_id defaults to "" — an unset kickoff must fail loud.
    with pytest.raises(ValueError, match="message_id"):
        flow.fetch()
    reader.get_message.assert_not_called()


def test_flow_fetch_wraps_gmail_dict_in_raw_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_token(monkeypatch)
    reader = MagicMock(spec=GmailReaderService)
    reader.get_message.return_value = {"id": "m1", "payload": {}}

    flow = MailIngestorFlow(reader=reader, model="c")
    flow.state.message_id = "m1"
    raw = flow.fetch()

    assert isinstance(raw, RawMessage)
    assert raw.message_id == "m1"
    assert raw.payload == {"id": "m1", "payload": {}}
    reader.get_message.assert_called_once_with("m1")


def test_flow_parse_produces_email_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_token(monkeypatch)
    reader = MagicMock(spec=GmailReaderService)

    flow = MailIngestorFlow(reader=reader, model="c")
    raw = RawMessage(message_id="m1", payload=_gmail_dict(subject="Hi", body="hello"))
    parsed = flow.parse(raw)

    assert isinstance(parsed, EmailMessage)
    assert parsed.sender == "alice@example.com"
    assert parsed.subject == "Hi"
    assert parsed.body_text.strip() == "hello"


# ---------- _truncate helper ----------


def test_truncate_returns_none_for_none() -> None:
    assert _truncate(None, 10) is None


def test_truncate_passes_short_text_through() -> None:
    assert _truncate("short", 10) == "short"


def test_truncate_appends_ellipsis_when_over_limit() -> None:
    assert _truncate("abcdefghijk", 5) == "abcde..."


# ---------- summarize failure paths ----------


def test_summarize_raises_when_llm_output_does_not_validate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Crew exhausts max_iter, `.pydantic` is None; the flow must surface
    a distinct exception carrying the raw LLM text for the DLQ record."""
    _seed_token(monkeypatch)
    fake_result = MagicMock(pydantic=None, raw="not-valid-json{...")

    def fake_crew_ctor(*_args: object, **_kwargs: object) -> MagicMock:
        instance = MagicMock()
        instance.kickoff.return_value = fake_result
        return instance

    monkeypatch.setattr("mail_ingestor.flow.Crew", fake_crew_ctor)

    reader = MagicMock(spec=GmailReaderService)
    flow = MailIngestorFlow(reader=reader, model="c")
    parsed = EmailMessage(
        message_id="m1",
        sender="a@x.com",
        received_at=datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
        subject="Test",
        body_text="hello",
    )

    with pytest.raises(SummaryValidationError, match="not-valid-json"):
        flow.summarize(parsed)


def test_summarize_error_truncates_long_raw_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Long LLM raw outputs are clipped in the error message so DLQ records
    do not carry unbounded prompts back through logging."""
    _seed_token(monkeypatch)
    long_raw = "x" * 500
    fake_result = MagicMock(pydantic=None, raw=long_raw)

    def fake_crew_ctor(*_args: object, **_kwargs: object) -> MagicMock:
        instance = MagicMock()
        instance.kickoff.return_value = fake_result
        return instance

    monkeypatch.setattr("mail_ingestor.flow.Crew", fake_crew_ctor)

    reader = MagicMock(spec=GmailReaderService)
    flow = MailIngestorFlow(reader=reader, model="c")
    parsed = EmailMessage(
        message_id="m1",
        sender="a@x.com",
        received_at=datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
    )

    with pytest.raises(SummaryValidationError) as excinfo:
        flow.summarize(parsed)
    message = str(excinfo.value)
    assert "..." in message
    assert message.count("x") <= 220  # 200 chars + minor overhead


# ---------- End-to-end kickoff (the Task 3.3 checkpoint) ----------


def test_kickoff_produces_valid_summary_record_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_token(monkeypatch)

    reader = MagicMock(spec=GmailReaderService)
    reader.get_message.return_value = _gmail_dict(subject="Weekly report", body="Sales up 20%")

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

    flow = MailIngestorFlow(reader=reader, model="claude-haiku-x")
    result = flow.kickoff(inputs={"message_id": "m1"})

    assert isinstance(result, SummaryRecord)
    assert result.source_message_id == "m1"
    assert result.subject == "Weekly report"
    assert result.model == "claude-haiku-x"
    assert result.summary.tl_dr == "Weekly sales up 20%."
    assert result.summary.category == "report"


def test_kickoff_end_to_end_exercises_the_real_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch the Anthropic client factory (not the adapter's `.call`) so the
    adapter's real message-conversion and response-parsing code runs.

    The monkeypatched E2E above proves the flow wires up; this one proves
    the NativeAnthropicLLM implementation is actually reachable end-to-end.
    """
    _seed_token(monkeypatch)

    reader = MagicMock(spec=GmailReaderService)
    reader.get_message.return_value = _gmail_dict(subject="Q4", body="Numbers up.")

    canned = json.dumps(
        {
            "tl_dr": "Q4 numbers up.",
            "summary": "Q4 report shows growth.",
            "key_points": ["growth"],
            "action_items": [],
            "category": "report",
        }
    )
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _mock_text_response(text=canned)
    monkeypatch.setattr("mail_ingestor.flow.get_llm_client", lambda: fake_client)

    flow = MailIngestorFlow(reader=reader, model="claude-x")
    result = flow.kickoff(inputs={"message_id": "m1"})

    assert isinstance(result, SummaryRecord)
    assert result.summary.tl_dr == "Q4 numbers up."
    fake_client.messages.create.assert_called()
    # Tools are not declared on the Agent yet, so none should leak through.
    for call in fake_client.messages.create.call_args_list:
        assert not call.kwargs.get("tools")


def test_native_llm_propagates_missing_auth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "")
    with pytest.raises(MissingSettingError, match="ANTHROPIC_AUTH_TOKEN"):
        NativeAnthropicLLM(model="c")


def test_kickoff_env_opts_out_of_crewai_telemetry() -> None:
    import os

    import mail_ingestor.flow  # noqa: F401  — imports set the env var

    assert os.environ.get("CREWAI_TELEMETRY_OPT_OUT") == "1"


# ---------- TokenUsage accumulator (Task 3.4) ----------


def _mock_response_with_usage(
    text: str = "ok",
    input_tokens: int | None = 100,
    output_tokens: int | None = 50,
    stop_reason: str = "end_turn",
    include_usage: bool = True,
) -> MagicMock:
    """Fake Anthropic response with a controllable ``.usage`` block."""
    block = MagicMock()
    block.text = text
    block.type = "text"
    response = MagicMock(content=[block], stop_reason=stop_reason)
    if include_usage:
        usage = MagicMock()
        usage.input_tokens = input_tokens
        usage.output_tokens = output_tokens
        response.usage = usage
    else:
        response.usage = None
    return response


def test_token_usage_starts_none_none() -> None:
    usage = TokenUsage()
    assert usage.prompt is None
    assert usage.completion is None


def test_token_usage_observe_accumulates_both_fields() -> None:
    usage = TokenUsage()
    usage.observe(5, 3)
    usage.observe(2, 1)
    assert usage.prompt == 7
    assert usage.completion == 4


def test_token_usage_observe_none_leaves_totals_untouched() -> None:
    usage = TokenUsage()
    usage.observe(10, 4)
    usage.observe(None, None)
    assert usage.prompt == 10
    assert usage.completion == 4


def test_token_usage_partial_observation_updates_known_field_only() -> None:
    usage = TokenUsage()
    usage.observe(5, None)
    assert usage.prompt == 5
    assert usage.completion is None
    usage.observe(None, 3)
    assert usage.prompt == 5
    assert usage.completion == 3


def test_token_usage_reset_returns_to_unobserved_state() -> None:
    usage = TokenUsage()
    usage.observe(10, 4)
    usage.reset()
    assert usage.prompt is None
    assert usage.completion is None


def test_native_llm_no_usage_sink_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    # Adapter must run cleanly when no usage sink is provided (the default),
    # so callers that don't care about accounting pay no penalty.
    _seed_token(monkeypatch)
    llm = NativeAnthropicLLM(model="claude-x")
    llm._client = MagicMock()
    llm._client.messages.create.return_value = _mock_response_with_usage(text="ok")
    assert llm.call("hi") == "ok"


def test_native_llm_records_usage_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_token(monkeypatch)
    usage = TokenUsage()
    llm = NativeAnthropicLLM(model="claude-x", usage_sink=usage)
    llm._client = MagicMock()
    llm._client.messages.create.return_value = _mock_response_with_usage(
        input_tokens=42, output_tokens=17
    )
    llm.call("say hi")
    assert usage.prompt == 42
    assert usage.completion == 17


def test_native_llm_accumulates_usage_across_two_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Task 3.4 AC: verify accumulation across Agent iterations by exercising
    # two sequential adapter calls with distinct usage blocks.
    _seed_token(monkeypatch)
    usage = TokenUsage()
    llm = NativeAnthropicLLM(model="claude-x", usage_sink=usage)
    llm._client = MagicMock()
    llm._client.messages.create.side_effect = [
        _mock_response_with_usage(text="step1", input_tokens=100, output_tokens=50),
        _mock_response_with_usage(text="step2", input_tokens=200, output_tokens=75),
    ]
    llm.call("first")
    llm.call("second")
    assert usage.prompt == 300
    assert usage.completion == 125


def test_native_llm_missing_usage_attribute_leaves_sink_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_token(monkeypatch)
    usage = TokenUsage()
    llm = NativeAnthropicLLM(model="claude-x", usage_sink=usage)
    llm._client = MagicMock()
    llm._client.messages.create.return_value = _mock_response_with_usage(include_usage=False)
    llm.call("hi")
    assert usage.prompt is None
    assert usage.completion is None


def test_native_llm_partial_usage_fields_update_only_known_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_token(monkeypatch)
    usage = TokenUsage()
    llm = NativeAnthropicLLM(model="claude-x", usage_sink=usage)
    llm._client = MagicMock()
    llm._client.messages.create.return_value = _mock_response_with_usage(
        input_tokens=50, output_tokens=None
    )
    llm.call("hi")
    assert usage.prompt == 50
    assert usage.completion is None


def test_make_token_step_callback_emits_debug_log_without_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    usage = TokenUsage()
    usage.observe(10, 5)
    callback = _make_token_step_callback(usage)
    fake_step = MagicMock()
    fake_step.__class__.__name__ = "AgentAction"
    with caplog.at_level("DEBUG", logger="mail_ingestor.flow"):
        callback(fake_step)
    matching = [r for r in caplog.records if "agent_step" in r.getMessage()]
    assert matching, "expected an agent_step debug record"
    message = matching[0].getMessage()
    assert "tokens_prompt=10" in message
    assert "tokens_completion=5" in message


def test_kickoff_populates_summary_record_with_token_totals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_token(monkeypatch)
    reader = MagicMock(spec=GmailReaderService)
    reader.get_message.return_value = _gmail_dict()

    canned = json.dumps(
        {
            "tl_dr": "t",
            "summary": "s",
            "key_points": [],
            "action_items": [],
            "category": "c",
        }
    )
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _mock_response_with_usage(
        text=canned, input_tokens=120, output_tokens=40
    )
    monkeypatch.setattr("mail_ingestor.flow.get_llm_client", lambda: fake_client)

    flow = MailIngestorFlow(reader=reader, model="claude-x")
    result = flow.kickoff(inputs={"message_id": "m1"})

    assert isinstance(result, SummaryRecord)
    assert result.tokens_prompt == 120
    assert result.tokens_completion == 40


def test_kickoff_missing_usage_yields_none_token_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_token(monkeypatch)
    reader = MagicMock(spec=GmailReaderService)
    reader.get_message.return_value = _gmail_dict()

    canned = json.dumps(
        {
            "tl_dr": "t",
            "summary": "s",
            "key_points": [],
            "action_items": [],
            "category": "c",
        }
    )
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _mock_response_with_usage(
        text=canned, include_usage=False
    )
    monkeypatch.setattr("mail_ingestor.flow.get_llm_client", lambda: fake_client)

    flow = MailIngestorFlow(reader=reader, model="claude-x")
    result = flow.kickoff(inputs={"message_id": "m1"})

    assert result.tokens_prompt is None
    assert result.tokens_completion is None


def test_kickoff_resets_accumulator_between_sequential_kickoffs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A reused Flow instance must not leak token totals from message N-1
    # into message N. The `summarize` step calls `reset()` before each run.
    _seed_token(monkeypatch)
    reader = MagicMock(spec=GmailReaderService)
    reader.get_message.return_value = _gmail_dict()

    canned = json.dumps(
        {
            "tl_dr": "t",
            "summary": "s",
            "key_points": [],
            "action_items": [],
            "category": "c",
        }
    )
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = [
        _mock_response_with_usage(text=canned, input_tokens=100, output_tokens=30),
        _mock_response_with_usage(text=canned, input_tokens=50, output_tokens=10),
    ]
    monkeypatch.setattr("mail_ingestor.flow.get_llm_client", lambda: fake_client)

    flow = MailIngestorFlow(reader=reader, model="claude-x")
    first = flow.kickoff(inputs={"message_id": "m1"})
    second = flow.kickoff(inputs={"message_id": "m2"})

    assert first.tokens_prompt == 100
    assert first.tokens_completion == 30
    assert second.tokens_prompt == 50
    assert second.tokens_completion == 10
