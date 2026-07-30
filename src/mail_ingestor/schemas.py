"""Boundary data contracts for the ingestion pipeline.

Framework-agnostic core — must not depend on crewai. These Pydantic v2 models
define the edges of the pipeline: the inbound parsed email (``EmailMessage``),
the LLM output (``Summary``), and the persisted record (``SummaryRecord``).
The failure boundary (``DeadLetterRecord``) is defined alongside them.

``frozen=True`` protects field rebinding; the contents of list-typed fields
remain technically mutable, which is acceptable for this PoC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    """Timezone-aware UTC now (used as a default_factory)."""
    return datetime.now(UTC)


class _StrictModel(BaseModel):
    """Immutable base: forbid unknown fields and mutation after construction.

    ``protected_namespaces=()`` disables Pydantic's ``model_*`` guard so the
    domain field ``SummaryRecord.model`` (the LLM model id) raises no warning.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())


class RawMessage(_StrictModel):
    """Gmail-fetched message before MIME parsing.

    Thin typed wrapper around the raw Gmail resource dict, used as the
    hand-off between the ``fetch`` and ``parse`` steps of the pipeline so
    intermediate state stays Pydantic-typed rather than a bare ``dict``.
    """

    message_id: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class EmailMessage(_StrictModel):
    """Inbound boundary: a parsed Gmail message handed to the pipeline."""

    message_id: str = Field(min_length=1)
    thread_id: str | None = None
    subject: str = ""
    sender: str
    recipients: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    received_at: AwareDatetime
    body_text: str = ""
    snippet: str | None = None
    attachment_filenames: list[str] = Field(default_factory=list)


class Summary(_StrictModel):
    """LLM output contract (used as ``output_pydantic``)."""

    tl_dr: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    key_points: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    category: str = Field(min_length=1)


class SummaryRecord(_StrictModel):
    """Outbound / persistence boundary: a ``Summary`` plus provenance."""

    source_message_id: str = Field(min_length=1)
    subject: str = ""
    summary: Summary
    model: str = Field(min_length=1)
    created_at: AwareDatetime = Field(default_factory=_utcnow)


class ProcessingStage(str, Enum):
    """Pipeline stage where processing of a message can fail."""

    READ = "read"
    PARSE = "parse"
    SUMMARIZE = "summarize"
    PERSIST = "persist"


class DeadLetterRecord(_StrictModel):
    """Failure boundary: a message that could not be processed."""

    source_message_id: str | None = None
    stage: ProcessingStage
    error: str = Field(min_length=1)
    failed_at: AwareDatetime = Field(default_factory=_utcnow)
