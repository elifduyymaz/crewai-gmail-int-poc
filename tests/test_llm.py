from __future__ import annotations

import re
from pathlib import Path

import anthropic
import pytest

from mail_ingestor import llm as llm_module
from mail_ingestor.config import MissingSettingError
from mail_ingestor.llm import get_llm_client

_LLM_MODULE = Path(__file__).resolve().parent.parent / "src" / "mail_ingestor" / "llm.py"
_CREWAI_IMPORT = re.compile(r"^\s*(?:from|import)\s+crewai(?:\b|\.)", re.MULTILINE)


def _isolated_env(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", token)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_get_llm_client_returns_anthropic_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolated_env(monkeypatch, "tok-test-abc")
    client = get_llm_client()
    assert isinstance(client, anthropic.Anthropic)


def test_get_llm_client_uses_auth_token_not_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolated_env(monkeypatch, "tok-test-abc")
    client = get_llm_client()
    assert client.auth_token == "tok-test-abc"
    assert client.api_key is None


def test_missing_auth_token_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "")
    with pytest.raises(MissingSettingError, match="ANTHROPIC_AUTH_TOKEN"):
        get_llm_client()


def test_llm_module_does_not_import_crewai() -> None:
    source = _LLM_MODULE.read_text(encoding="utf-8")
    assert not _CREWAI_IMPORT.search(source), "llm.py must stay outside the crewai fence."


def test_mock_llm_client_fixture_patches_factory(mock_llm_client) -> None:  # type: ignore[no-untyped-def]
    assert llm_module.get_llm_client() is mock_llm_client
