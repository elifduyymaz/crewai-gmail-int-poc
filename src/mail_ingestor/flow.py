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

import os

os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "1")

from typing import Any

from crewai import Agent, Crew, Process, Task
from crewai.flow import Flow, listen, start
from crewai.llms.base_llm import BaseLLM
from crewai.utilities.types import LLMMessage
from pydantic import BaseModel

from mail_ingestor.gmail.labels import LabelResolver
from mail_ingestor.gmail.parser import parse_gmail_message
from mail_ingestor.gmail.reader import GmailReaderService
from mail_ingestor.llm import get_llm_client
from mail_ingestor.schemas import (
    EmailMessage,
    RawMessage,
    Summary,
    SummaryRecord,
)
from mail_ingestor.tools.gmail_tool import (
    GmailGetMessageTool,
    GmailListByLabelTool,
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


class NativeAnthropicLLM(BaseLLM):
    """CrewAI ``BaseLLM`` backed by the native Anthropic SDK.

    Runs the native client from ``mail_ingestor.llm`` (built with an
    ``auth_token`` seed credential); does not go through litellm.
    """

    def __init__(self, model: str) -> None:
        super().__init__(model=model)
        self._client = get_llm_client()

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
        turns, system = _to_anthropic_messages(messages)
        create_kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": _DEFAULT_MAX_TOKENS,
            "messages": turns,
        }
        if system is not None:
            create_kwargs["system"] = system
        response = self._client.messages.create(**create_kwargs)
        return "".join(getattr(block, "text", "") for block in response.content)


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


def build_summarizer_agent(
    model: str,
    reader: GmailReaderService,
    resolver: LabelResolver,
) -> Agent:
    """Construct the single-role summarizer Agent with the native LLM adapter."""
    return Agent(
        role=_SUMMARIZER_ROLE,
        goal=_SUMMARIZER_GOAL,
        backstory=_SUMMARIZER_BACKSTORY,
        llm=NativeAnthropicLLM(model=model),
        tools=[
            GmailListByLabelTool(reader=reader, resolver=resolver),
            GmailGetMessageTool(reader=reader),
        ],
        max_iter=_AGENT_MAX_ITER,
        max_execution_time=_AGENT_MAX_EXECUTION_TIME_SECONDS,
        memory=False,
        cache=False,
        allow_delegation=False,
        verbose=False,
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
        resolver: LabelResolver,
        model: str,
    ) -> None:
        super().__init__()
        self._reader = reader
        self._resolver = resolver
        self._model = model

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
        agent = build_summarizer_agent(self._model, self._reader, self._resolver)
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
            raise RuntimeError(  # noqa: TRY004  — runtime state failure, not user-input type error
                "Crew produced no Summary — the LLM output did not validate "
                "against output_pydantic within Agent.max_iter."
            )
        record = SummaryRecord(
            source_message_id=parsed.message_id,
            subject=parsed.subject,
            summary=summary,
            model=self._model,
        )
        self.state.summary_record = record
        return record
