"""Shared pytest fixtures for the mail-ingestor test suite."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_llm_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``mail_ingestor.llm.get_llm_client`` with a ``MagicMock`` stand-in.

    Downstream flow tests inject a fake LLM client here so unit runs never hit
    the real Anthropic API. The returned object is the same instance that
    ``get_llm_client()`` will yield during the test.
    """
    fake = MagicMock(name="AnthropicClient")
    monkeypatch.setattr("mail_ingestor.llm.get_llm_client", lambda: fake)
    return fake
