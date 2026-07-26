from dataclasses import FrozenInstanceError

import pytest

from mail_ingestor.config import DEFAULT_LLM_MODEL, MissingSettingError, Settings


def test_default_llm_model_constant():
    assert DEFAULT_LLM_MODEL == "claude-sonnet-5"


def test_from_env_uses_default_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    settings = Settings.from_env(load_dotenv_file=False)
    assert settings.anthropic_api_key == "sk-test-123"
    assert settings.llm_model == "claude-sonnet-5"


def test_from_env_honors_llm_model_override(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-8")
    settings = Settings.from_env(load_dotenv_file=False)
    assert settings.llm_model == "claude-opus-4-8"


def test_from_env_missing_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MissingSettingError, match="ANTHROPIC_API_KEY"):
        Settings.from_env(load_dotenv_file=False)


def test_from_env_blank_key_raises(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    with pytest.raises(MissingSettingError):
        Settings.from_env(load_dotenv_file=False)


def test_settings_is_frozen():
    settings = Settings(anthropic_api_key="sk-x")
    with pytest.raises(FrozenInstanceError):
        settings.llm_model = "other"


def test_repr_masks_api_key():
    settings = Settings(anthropic_api_key="sk-super-secret")
    assert "sk-super-secret" not in repr(settings)
    assert "sk-super-secret" not in str(settings)
