from dataclasses import FrozenInstanceError

import pytest

from mail_ingestor.config import DEFAULT_LLM_MODEL, MissingSettingError, Settings


def test_default_llm_model_constant():
    assert DEFAULT_LLM_MODEL == "claude-haiku-4-5-20251001"


def test_from_env_uses_default_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test-123")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    settings = Settings.from_env(load_dotenv_file=False)
    assert settings.anthropic_auth_token == "tok-test-123"
    assert settings.llm_model == "claude-haiku-4-5-20251001"


def test_from_env_honors_llm_model_override(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test-123")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-8")
    settings = Settings.from_env(load_dotenv_file=False)
    assert settings.llm_model == "claude-opus-4-8"


def test_from_env_missing_token_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(MissingSettingError, match="ANTHROPIC_AUTH_TOKEN"):
        Settings.from_env(load_dotenv_file=False)


def test_from_env_blank_token_raises(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "   ")
    with pytest.raises(MissingSettingError, match="ANTHROPIC_AUTH_TOKEN"):
        Settings.from_env(load_dotenv_file=False)


def test_no_api_key_field():
    # This project uses a Claude seed token, never a static API key.
    assert not hasattr(Settings(anthropic_auth_token="tok-x"), "anthropic_api_key")


def test_settings_is_frozen():
    settings = Settings(anthropic_auth_token="tok-x")
    with pytest.raises(FrozenInstanceError):
        settings.llm_model = "other"


def test_repr_masks_auth_token():
    settings = Settings(anthropic_auth_token="tok-super-secret")
    assert "tok-super-secret" not in repr(settings)
    assert "tok-super-secret" not in str(settings)
