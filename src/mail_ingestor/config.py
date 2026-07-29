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

from dotenv import load_dotenv

DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"


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

    @classmethod
    def from_env(cls, *, load_dotenv_file: bool = True) -> Settings:
        """Build ``Settings`` from environment variables.

        Reads ``ANTHROPIC_AUTH_TOKEN`` (required — a Claude OAuth/seed token) and
        ``LLM_MODEL`` (optional, defaults to ``DEFAULT_LLM_MODEL``). This project does
        not use ``ANTHROPIC_API_KEY``.
        """
        if load_dotenv_file:
            load_dotenv()
        auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
        if not auth_token:
            raise MissingSettingError(
                "ANTHROPIC_AUTH_TOKEN is required but is unset or blank (see .env.example)."
            )
        llm_model = os.environ.get("LLM_MODEL", "").strip() or DEFAULT_LLM_MODEL
        return cls(anthropic_auth_token=auth_token, llm_model=llm_model)
