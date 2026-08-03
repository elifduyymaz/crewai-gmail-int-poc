from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from mail_ingestor.config import (
    DEFAULT_LLM_MODEL,
    DEFAULT_LOG_LEVEL,
    DEFAULT_SQLITE_DB_PATH,
    MissingSettingError,
    Settings,
)


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


def test_default_sqlite_db_path_constant():
    assert DEFAULT_SQLITE_DB_PATH == Path("mail_ingestor.db")


def test_from_env_uses_default_sqlite_db_path(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test-123")
    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    settings = Settings.from_env(load_dotenv_file=False)
    assert settings.sqlite_db_path == DEFAULT_SQLITE_DB_PATH


def test_from_env_honors_sqlite_db_path_override(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test-123")
    monkeypatch.setenv("SQLITE_DB_PATH", "/tmp/other-vault.db")
    settings = Settings.from_env(load_dotenv_file=False)
    assert settings.sqlite_db_path == Path("/tmp/other-vault.db")


def test_default_log_level_constant():
    assert DEFAULT_LOG_LEVEL == "INFO"


def test_from_env_uses_default_log_level(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test-123")
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    settings = Settings.from_env(load_dotenv_file=False)
    assert settings.log_level == "INFO"


def test_from_env_honors_log_level_override(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test-123")
    monkeypatch.setenv("LOG_LEVEL", "debug")  # case-insensitive
    settings = Settings.from_env(load_dotenv_file=False)
    assert settings.log_level == "DEBUG"


def test_from_env_rejects_invalid_log_level(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test-123")
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")  # not a stdlib logging level
    with pytest.raises(MissingSettingError, match="LOG_LEVEL"):
        Settings.from_env(load_dotenv_file=False)
