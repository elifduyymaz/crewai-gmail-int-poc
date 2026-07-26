"""Application settings loaded from environment variables.

Framework-agnostic core — a plain stdlib dataclass with no framework coupling.
Only the LLM settings are defined here; Gmail and persistence settings land with
the tasks that consume them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

DEFAULT_LLM_MODEL = "claude-sonnet-5"


class MissingSettingError(RuntimeError):
    """Raised when a required environment variable is unset or blank."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable application settings.

    ``anthropic_api_key`` is excluded from ``repr`` so the secret never leaks into
    logs or tracebacks.
    """

    anthropic_api_key: str = field(repr=False)
    llm_model: str = DEFAULT_LLM_MODEL

    @classmethod
    def from_env(cls, *, load_dotenv_file: bool = True) -> Settings:
        """Build ``Settings`` from environment variables.

        Loads a local ``.env`` first (unless ``load_dotenv_file`` is False), then reads
        ``ANTHROPIC_API_KEY`` (required) and ``LLM_MODEL`` (optional, defaults to
        ``DEFAULT_LLM_MODEL``).
        """
        if load_dotenv_file:
            load_dotenv()
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise MissingSettingError(
                "ANTHROPIC_API_KEY is required but is unset or blank (see .env.example)."
            )
        llm_model = os.environ.get("LLM_MODEL", "").strip() or DEFAULT_LLM_MODEL
        return cls(anthropic_api_key=api_key, llm_model=llm_model)
