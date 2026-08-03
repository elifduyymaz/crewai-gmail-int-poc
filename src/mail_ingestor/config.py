"""Application settings loaded from environment variables.

Framework-agnostic core — a plain stdlib dataclass with no framework coupling.
Only the LLM settings are defined here; Gmail and persistence settings land with
the tasks that consume them.

Authentication uses a Claude OAuth/seed token (``ANTHROPIC_AUTH_TOKEN``), NOT a
static ``ANTHROPIC_API_KEY``. The token is handed to the Anthropic SDK as
``auth_token`` by the code that constructs the client.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_SQLITE_DB_PATH = Path("mail_ingestor.db")
DEFAULT_LOG_LEVEL = "INFO"
_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class MissingSettingError(RuntimeError):
    """Raised when a required environment variable is unset or blank."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable application settings.

    ``anthropic_auth_token`` (a Claude OAuth/seed token) is excluded from ``repr``
    so the secret never leaks into logs or tracebacks.
    """

    anthropic_auth_token: str = field(repr=False)
    llm_model: str = DEFAULT_LLM_MODEL
    sqlite_db_path: Path = DEFAULT_SQLITE_DB_PATH
    log_level: str = DEFAULT_LOG_LEVEL

    @classmethod
    def from_env(cls, *, load_dotenv_file: bool = True) -> Settings:
        """Build ``Settings`` from environment variables.

        Reads ``ANTHROPIC_AUTH_TOKEN`` (required — a Claude OAuth/seed token),
        ``LLM_MODEL`` (optional, defaults to ``DEFAULT_LLM_MODEL``),
        ``SQLITE_DB_PATH`` (optional, defaults to ``DEFAULT_SQLITE_DB_PATH``),
        and ``LOG_LEVEL`` (optional, defaults to ``INFO``; validated against
        ``_VALID_LOG_LEVELS``). This project does not use ``ANTHROPIC_API_KEY``.
        """
        if load_dotenv_file:
            load_dotenv()
        auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
        if not auth_token:
            raise MissingSettingError(
                "ANTHROPIC_AUTH_TOKEN is required but is unset or blank (see .env.example)."
            )
        llm_model = os.environ.get("LLM_MODEL", "").strip() or DEFAULT_LLM_MODEL
        db_path_raw = os.environ.get("SQLITE_DB_PATH", "").strip()
        sqlite_db_path = Path(db_path_raw) if db_path_raw else DEFAULT_SQLITE_DB_PATH
        log_level_raw = os.environ.get("LOG_LEVEL", "").strip().upper() or DEFAULT_LOG_LEVEL
        if log_level_raw not in _VALID_LOG_LEVELS:
            raise MissingSettingError(
                f"LOG_LEVEL={log_level_raw!r} is not one of "
                f"{sorted(_VALID_LOG_LEVELS)} (see .env.example)."
            )
        return cls(
            anthropic_auth_token=auth_token,
            llm_model=llm_model,
            sqlite_db_path=sqlite_db_path,
            log_level=log_level_raw,
        )
