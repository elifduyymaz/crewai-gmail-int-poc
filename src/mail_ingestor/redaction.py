"""PII/PCI redaction stub.

Framework-agnostic core — no CrewAI dependency. This is a STUB: it masks email
addresses only. Comprehensive PII/PCI redaction (card numbers, SSN, phone
numbers, names, ...) is production-scope and belongs in the hardened build —
add entries to ``_PATTERNS`` to extend it.
"""

from __future__ import annotations

import re

EMAIL_PLACEHOLDER = "[REDACTED_EMAIL]"

# Pragmatic email matcher (stub-grade — not full RFC 5322).
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# (compiled pattern, replacement). Extend for production PII/PCI redaction.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_EMAIL_RE, EMAIL_PLACEHOLDER),
]


def redact(text: str) -> str:
    """Return ``text`` with known PII/PCI patterns masked (stub: emails only)."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
