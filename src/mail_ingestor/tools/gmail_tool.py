"""CrewAI ``BaseTool`` wrappers for the framework-agnostic Gmail services.

One of the two files in the package allowed to import ``crewai`` (the other
being ``crew/``); enforced by the ``no-crewai-in-core`` pre-commit hook and
``tests/test_discipline.py``. Adapts ``GmailReaderService`` and
``LabelResolver`` into tools an Agent can call by name.
"""

from __future__ import annotations

from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from mail_ingestor.gmail.labels import LabelResolver
from mail_ingestor.gmail.reader import GmailReaderService


class _ListByLabelArgs(BaseModel):
    label_name: str = Field(..., description="Gmail label name (e.g. 'INBOX') to list from.")
    limit: int = Field(..., description="Maximum number of message ids to return.")


class GmailListByLabelTool(BaseTool):
    name: str = "gmail_list_by_label"
    description: str = (
        "List Gmail message ids under a given label name, capped at `limit`. "
        "Resolves the label name to an id via the injected LabelResolver."
    )
    args_schema: type[BaseModel] = _ListByLabelArgs
    env_vars: list[Any] = Field(default_factory=list)
    reader: GmailReaderService
    resolver: LabelResolver

    def _run(self, label_name: str, limit: int) -> list[str]:
        label_id = self.resolver.resolve(label_name)
        return self.reader.list_message_ids(label_id, max_results=limit)


class _GetMessageArgs(BaseModel):
    message_id: str = Field(..., description="Gmail message id, as returned by the list tool.")


class GmailGetMessageTool(BaseTool):
    name: str = "gmail_get_message"
    description: str = (
        "Fetch a Gmail message by id; returns the raw Gmail resource dict "
        "(format=full, with payload headers and parts). MIME parsing is a "
        "separate downstream step, not this tool's responsibility."
    )
    args_schema: type[BaseModel] = _GetMessageArgs
    env_vars: list[Any] = Field(default_factory=list)
    reader: GmailReaderService

    def _run(self, message_id: str) -> dict[str, Any]:
        return self.reader.get_message(message_id)
