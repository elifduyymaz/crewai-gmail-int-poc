from __future__ import annotations

import re
from pathlib import Path

import anthropic
import pytest

from mail_ingestor import llm as llm_module
from mail_ingestor.config import MissingSettingError
from mail_ingestor.llm import get_llm_client, get_llm_config

_LLM_MODULE = Path(__file__).resolve().parent.parent / "src" / "mail_ingestor" / "llm.py"
_CREWAI_IMPORT = re.compile(r"^\s*(?:from|import)\s+crewai(?:\b|\.)", re.MULTILINE)


def _isolated_env(monkeypatch: pytest.MonkeyPatch, *, token: str, model: str | None) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", token)
    if model is None:
        monkeypatch.delenv("LLM_MODEL", raising=False)
    else:
        monkeypatch.setenv("LLM_MODEL", model)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_get_llm_client_returns_anthropic_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolated_env(monkeypatch, token="tok-test-abc", model=None)
    client = get_llm_client()
    assert isinstance(client, anthropic.Anthropic)


def test_get_llm_client_uses_auth_token_not_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolated_env(monkeypatch, token="tok-test-abc", model=None)
    client = get_llm_client()
    assert client.auth_token == "tok-test-abc"
    assert client.api_key is None


def test_get_llm_config_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolated_env(monkeypatch, token="tok-test-abc", model="claude-opus-4-8")
    cfg = get_llm_config()
    assert set(cfg) == {"model", "client"}
    assert cfg["model"] == "claude-opus-4-8"
    assert isinstance(cfg["client"], anthropic.Anthropic)
    assert cfg["client"].auth_token == "tok-test-abc"


def test_get_llm_config_uses_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolated_env(monkeypatch, token="tok-test-abc", model=None)
    cfg = get_llm_config()
    assert cfg["model"] == "claude-sonnet-5"


def test_missing_auth_token_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(MissingSettingError, match="ANTHROPIC_AUTH_TOKEN"):
        get_llm_client()
    with pytest.raises(MissingSettingError, match="ANTHROPIC_AUTH_TOKEN"):
        get_llm_config()


def test_llm_module_does_not_import_crewai() -> None:
    source = _LLM_MODULE.read_text(encoding="utf-8")
    assert not _CREWAI_IMPORT.search(source), "llm.py must stay outside the crewai fence (NFR-I2)."


def test_mock_llm_client_fixture_patches_factory(mock_llm_client) -> None:  # type: ignore[no-untyped-def]
    assert llm_module.get_llm_client() is mock_llm_client
