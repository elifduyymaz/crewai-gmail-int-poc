"""Native LLM provider client factory.

Sole module in the package that constructs the Anthropic SDK client — no other
module imports ``anthropic`` (enforced by ``tests/test_discipline.py``).
Deliberately does not ``import crewai``: framework wrapping (a ``BaseLLM``
subclass that consumes this client) lives in ``flow.py``, keeping the
provider abstraction outside the framework fence.

Authentication uses a Claude OAuth/seed token
(``Settings.anthropic_auth_token``) handed to the SDK as ``auth_token``;
``ANTHROPIC_API_KEY`` is not used.
"""

from __future__ import annotations

import anthropic

from mail_ingestor.config import Settings


def get_llm_client() -> anthropic.Anthropic:
    """Return an Anthropic client authenticated with the Claude seed token."""
    settings = Settings.from_env()
    return anthropic.Anthropic(auth_token=settings.anthropic_auth_token)
