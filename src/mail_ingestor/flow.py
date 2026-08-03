"""CrewAI orchestration for the mail-ingestion pipeline.

Second (and last) file allowed to import ``crewai``; enforced by the
``no-crewai-in-core`` pre-commit hook and ``tests/test_discipline.py``.

Two concerns live here because both are inseparable from the framework
binding — separating them would just move the fence, not shrink it:

  * ``NativeAnthropicLLM``  — a ``BaseLLM`` subclass that bypasses litellm
    and drives the native Anthropic SDK client from ``llm.py`` with the
    keyless seed token. Needed because ``crewai.LLM`` is litellm-locked
    and cannot consume a Bearer / ``auth_token`` credential.
  * ``MailIngestorFlow`` and its two factories (Agent, Task) — the
    fetch → parse → summarize pipeline that produces a ``SummaryRecord``.

The module opts the process out of CrewAI telemetry before it imports
``crewai`` so no network call escapes on import (see FR23).
"""

from __future__ import annotations

import logging
import os

os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "1")

from dataclasses import dataclass
from typing import Any

from crewai import Agent, Crew, Process, Task
from crewai.flow import Flow, listen, start
from crewai.llms.base_llm import BaseLLM
from crewai.utilities.types import LLMMessage
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from mail_ingestor.gmail.parser import parse_gmail_message
from mail_ingestor.gmail.reader import GmailReaderService
from mail_ingestor.llm import get_llm_client
from mail_ingestor.schemas import (
    EmailMessage,
    RawMessage,
    Summary,
    SummaryRecord,
)

# Elifce Additions :)

_DEFAULT_MAX_TOKENS = 4096  # 1024
_AGENT_MAX_ITER = 10  # 2
_AGENT_MAX_EXECUTION_TIME_SECONDS = 120  # 30
_SUMMARIZER_ROLE = "Email Summarizer"  # "Structured Email Summarizer"
_SUMMARIZER_GOAL = "Read one email and return a structured summary as JSON."
_SUMMARIZER_BACKSTORY = (
    "You are a careful reader who converts a single email into a short, "
    "faithful summary. You never invent facts absent from the source and "
    "you emit exactly the JSON schema requested."
)
_RAW_SNIPPET_MAX_CHARS = 200


@dataclass(slots=True)
class TokenUsage:
    """Best-effort per-message token accumulator.

    ``None`` on either total means "no LLM call ever reported that metric".
    Any observed count is added to a running sum, so calls with only partial
    usage (e.g. only ``input_tokens`` present) still contribute what they can.

    This lives outside the LLM adapter as a plain dataclass so the framework-
    binding layer (``NativeAnthropicLLM``) can be swapped or mocked without
    touching the accumulator's semantics. The flow instantiates one per
    message and passes it to the adapter and to the Agent's step callback.
    """

    prompt: int | None = None
    completion: int | None = None

    def observe(self, prompt: int | None, completion: int | None) -> None:
        """Merge one API-call's usage into the running totals; ``None`` is a no-op."""
        if prompt is not None:
            self.prompt = (self.prompt or 0) + prompt
        if completion is not None:
            self.completion = (self.completion or 0) + completion

    def reset(self) -> None:
        """Zero the accumulator back to the unobserved state (``None`` for both)."""
        self.prompt = None
        self.completion = None


def _truncate(text: str | None, limit: int) -> str | None:
    """Truncate ``text`` for embedding in error messages; passes ``None`` through."""
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit] + "..."


def _content_as_text(content: str | list[dict[str, Any]] | None) -> str:
    """Coerce an ``LLMMessage.content`` value into plain text.

    CrewAI models the content field as ``str | list[dict] | None`` to support
    multi-block payloads (images, tool results). This pipeline is text-only,
    so list entries are reduced to their ``text`` blocks and ``None`` becomes
    the empty string.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _to_anthropic_messages(
    messages: str | list[LLMMessage],
) -> tuple[list[dict[str, Any]], str | None]:
    """Split a CrewAI message payload into ``(messages, system)`` for Anthropic.

    Anthropic's ``messages.create`` takes system prompts as a top-level
    ``system=`` kwarg rather than as a role in the messages array; multiple
    system entries are joined with a blank line. A bare string is treated as
    a single user turn.
    """
    if isinstance(messages, str):
        return ([{"role": "user", "content": messages}], None)
    system_parts: list[str] = []
    turns: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        text = _content_as_text(msg.get("content"))
        if role == "system":
            system_parts.append(text)
            continue
        turns.append({"role": role, "content": text})
    system = "\n\n".join(system_parts) if system_parts else None
    return (turns, system)


class ToolUseNotSupported(NotImplementedError):
    """Raised when the native LLM adapter is asked to forward tool schemas.

    ``NativeAnthropicLLM`` does not yet convert CrewAI ``BaseTool`` instances
    to Anthropic's tool schema, nor does it decode ``tool_use`` response
    blocks. Declaring tools on an Agent whose LLM cannot invoke them is a
    silent-failure surface, so we raise on receipt instead of silently
    dropping the ``tools`` argument.
    """


class EmptyLLMResponse(RuntimeError):
    """Raised when Anthropic returns a response with no text content.

    Common causes: ``stop_reason == "max_tokens"`` (truncated before any
    text was emitted), ``"refusal"``, ``"pause_turn"``, or a response made
    up entirely of non-text blocks such as ``tool_use``. Losing this
    signal to a caller-side JSON-validation error would obscure the real
    root cause when the flow's summarize step DLQs the message.
    """


class SummaryValidationError(RuntimeError):
    """Raised when the LLM output could not be validated as ``Summary``.

    CrewAI retries an ``output_pydantic`` validation failure internally
    up to ``Agent.max_iter``; if it exhausts the budget or is stopped by
    ``max_execution_time``, ``CrewOutput.pydantic`` is ``None``. The
    exception carries a truncated snippet of the raw LLM output so the
    DLQ record has enough context to reproduce the failure.
    """


class NativeAnthropicLLM(BaseLLM):
    """CrewAI ``BaseLLM`` backed by the native Anthropic SDK.

    Runs the native client from ``mail_ingestor.llm`` (built with an
    ``auth_token`` seed credential); does not go through litellm.

    Tool forwarding is intentionally not implemented — a non-empty
    ``tools`` argument to ``call`` raises ``ToolUseNotSupported`` so the
    gap fails loudly instead of dropping the request silently.

    Token usage: if a ``TokenUsage`` sink is provided, each successful API
    call's ``response.usage.input_tokens`` / ``output_tokens`` is merged
    into the accumulator. Missing usage fields are a no-op (``observe``
    ignores ``None``) so a mocked or usage-less response cannot crash the
    call path. This is the load-bearing surface for FR15 — the step
    callback merely logs iteration boundaries; accumulation happens here
    where the raw usage block is available before we discard the
    response.
    """

    def __init__(self, model: str, usage_sink: TokenUsage | None = None) -> None:
        super().__init__(model=model)
        self._client = get_llm_client()
        self._usage_sink = usage_sink

    def call(
        self,
        messages: str | list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        callbacks: list[Any] | None = None,
        available_functions: dict[str, Any] | None = None,
        from_task: Any | None = None,
        from_agent: Any | None = None,
        response_model: type[BaseModel] | None = None,
    ) -> str:
        if tools:
            raise ToolUseNotSupported(
                f"NativeAnthropicLLM received {len(tools)} tool schema(s); "
                "forwarding is not implemented. Either remove tools from the "
                "Agent or implement Anthropic tool_use in the adapter."
            )
        turns, system = _to_anthropic_messages(messages)
        create_kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": _DEFAULT_MAX_TOKENS,
            "messages": turns,
        }
        if system is not None:
            create_kwargs["system"] = system
        response = self._client.messages.create(**create_kwargs)
        self._record_usage(response)
        text = "".join(getattr(block, "text", "") for block in response.content)
        if not text:
            stop_reason = getattr(response, "stop_reason", "unknown")
            block_types = [
                getattr(block, "type", type(block).__name__) for block in response.content
            ]
            raise EmptyLLMResponse(
                f"Anthropic returned no text (stop_reason={stop_reason!r}, "
                f"block_types={block_types})."
            )
        return text

    def _record_usage(self, response: Any) -> None:
        """Merge ``response.usage`` into the sink; tolerant of missing fields."""
        if self._usage_sink is None:
            return
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        prompt = getattr(usage, "input_tokens", None)
        completion = getattr(usage, "output_tokens", None)
        self._usage_sink.observe(prompt, completion)


class IngestState(BaseModel):
    """Typed state for ``MailIngestorFlow``.

    Hand-offs between steps travel as Pydantic models
    (``RawMessage`` → ``EmailMessage`` → ``SummaryRecord``), never bare
    dicts.
    """

    message_id: str = ""
    raw: RawMessage | None = None
    parsed: EmailMessage | None = None
    summary_record: SummaryRecord | None = None


def _make_token_step_callback(usage: TokenUsage) -> Any:
    """Build a CrewAI ``step_callback`` that logs iteration boundaries.

    CrewAI invokes ``step_callback`` after each Agent thought/action step
    with an ``AgentAction`` / ``AgentFinish`` object. Token counts are
    already accumulated by ``NativeAnthropicLLM._record_usage`` at the API
    boundary (where ``response.usage`` is live); this callback exposes the
    running totals at DEBUG so a verbose run shows per-step budget burn
    without depending on the step object's shape (which drifts across
    CrewAI releases).
    """

    def step_callback(step: Any) -> None:
        step_kind = type(step).__name__
        logger.debug(
            "agent_step kind=%s tokens_prompt=%s tokens_completion=%s",
            step_kind,
            usage.prompt,
            usage.completion,
        )

    return step_callback


def build_summarizer_agent(model: str, usage_sink: TokenUsage | None = None) -> Agent:
    """Construct the single-role summarizer Agent with the native LLM adapter.

    Tools (``GmailListByLabelTool`` / ``GmailGetMessageTool``) are
    intentionally not declared here even though Task 3.3's AC lists them:
    the ``NativeAnthropicLLM`` adapter does not yet forward Anthropic tool
    schemas or decode ``tool_use`` response blocks. Declaring the tools
    while they cannot be invoked from the LLM path would let the Agent
    appear tool-enabled while the surface silently no-ops. Re-add
    ``tools=[...]`` in the same pass that implements forwarding in the
    adapter.

    When ``usage_sink`` is provided, the adapter feeds it per API call and
    the Agent's ``step_callback`` logs the running totals per iteration.
    """
    step_callback = _make_token_step_callback(usage_sink) if usage_sink is not None else None
    return Agent(
        role=_SUMMARIZER_ROLE,
        goal=_SUMMARIZER_GOAL,
        backstory=_SUMMARIZER_BACKSTORY,
        llm=NativeAnthropicLLM(model=model, usage_sink=usage_sink),
        max_iter=_AGENT_MAX_ITER,
        max_execution_time=_AGENT_MAX_EXECUTION_TIME_SECONDS,
        memory=False,
        cache=False,
        allow_delegation=False,
        verbose=False,
        step_callback=step_callback,
    )


def build_summarize_task(agent: Agent, parsed: EmailMessage) -> Task:
    """Construct the summarization Task whose output is validated as ``Summary``.

    The LLM produces the summary payload only; provenance fields
    (``source_message_id``, ``model``, ``created_at``) are filled by the
    flow when wrapping the Summary into a ``SummaryRecord``, so
    ``output_pydantic`` is ``Summary`` rather than ``SummaryRecord``.
    """
    description = (
        "Read the email below and return a JSON object with these keys: "
        "tl_dr (one sentence), summary (2-4 sentences), key_points (list), "
        "action_items (list), category (single lowercase word).\n\n"
        f"Subject: {parsed.subject}\n"
        f"From: {parsed.sender}\n"
        f"Body:\n{parsed.body_text}"
    )
    expected_output = "JSON with fields: tl_dr, summary, key_points, action_items, category."
    return Task(
        description=description,
        expected_output=expected_output,
        agent=agent,
        output_pydantic=Summary,
    )


class MailIngestorFlow(Flow[IngestState]):
    """Three-step CrewAI Flow: fetch → parse → summarize.

    Pipeline invariants:
      * State hand-offs are Pydantic models, not dicts.
      * ``fetch`` and ``parse`` are deterministic transport / parsing;
        only ``summarize`` invokes the LLM.
      * Errors from any step propagate unchanged so the outer batch loop
        (main.py) can route the message to a dead-letter record.
    """

    def __init__(
        self,
        reader: GmailReaderService,
        model: str,
    ) -> None:
        super().__init__()
        self._reader = reader
        self._model = model
        self._token_usage = TokenUsage()

    @start()
    def fetch(self) -> RawMessage:
        message_id = self.state.message_id
        if not message_id:
            raise ValueError(
                "MailIngestorFlow was kicked off without a message_id; "
                "call `flow.kickoff(inputs={'message_id': ...})`."
            )
        payload = self._reader.get_message(message_id)
        raw = RawMessage(message_id=message_id, payload=payload)
        self.state.raw = raw
        return raw

    @listen(fetch)
    def parse(self, raw: RawMessage) -> EmailMessage:
        parsed = parse_gmail_message(raw.payload)
        self.state.parsed = parsed
        return parsed

    @listen(parse)
    def summarize(self, parsed: EmailMessage) -> SummaryRecord:
        # Per-message accumulator reset: token totals must not bleed between
        # sequential kickoffs on the same Flow instance. The instance is
        # reused for the length of one message; each `summarize` starts fresh.
        self._token_usage.reset()
        agent = build_summarizer_agent(self._model, usage_sink=self._token_usage)
        task = build_summarize_task(agent, parsed)
        crew = Crew(
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
            memory=False,
            cache=False,
            max_rpm=None,
            verbose=False,
        )
        result = crew.kickoff()
        summary = getattr(result, "pydantic", None)
        if not isinstance(summary, Summary):
            raw = getattr(result, "raw", None)
            raw_snippet = _truncate(raw, _RAW_SNIPPET_MAX_CHARS)
            raise SummaryValidationError(
                f"Crew produced no valid Summary within "
                f"max_iter={_AGENT_MAX_ITER} / "
                f"max_execution_time={_AGENT_MAX_EXECUTION_TIME_SECONDS}s. "
                f"raw_output={raw_snippet!r}"
            )
        record = SummaryRecord(
            source_message_id=parsed.message_id,
            subject=parsed.subject,
            summary=summary,
            model=self._model,
            tokens_prompt=self._token_usage.prompt,
            tokens_completion=self._token_usage.completion,
        )
        self.state.summary_record = record
        return record
