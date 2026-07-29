"""Native LLM provider client factory (NFR-I2 boundary).

Sole module that constructs the Anthropic SDK client. Provider swap = edit this
file + ``pyproject.toml`` + ``.env.example``; no other module in the package
imports ``anthropic`` (enforced by ``tests/test_discipline.py`` and the
``no-crewai-in-core`` pre-commit hook). This module deliberately does not
``import crewai`` — the ``crewai.LLM`` wrapping happens under ``crew/`` so the
provider abstraction stays outside the framework fence.

Authentication uses a Claude OAuth/seed token (``Settings.anthropic_auth_token``)
handed to the SDK as ``auth_token``; ``ANTHROPIC_API_KEY`` is not used.
"""

from __future__ import annotations

from typing import Any

import anthropic

from mail_ingestor.config import Settings


def _build_client(settings: Settings) -> anthropic.Anthropic:
    return anthropic.Anthropic(auth_token=settings.anthropic_auth_token)


def get_llm_client() -> anthropic.Anthropic:
    """Return an Anthropic client authenticated with the Claude seed token."""
    return _build_client(Settings.from_env())


def get_llm_config() -> dict[str, Any]:
    """Return the kwargs shape accepted by ``crewai.LLM(**cfg)`` at Agent build time."""
    settings = Settings.from_env()
    return {"model": settings.llm_model, "client": _build_client(settings)}
