"""PII/PCI redaction stub.

Framework-agnostic core — no CrewAI dependency. This is a STUB: it masks email
addresses only. Comprehensive PII/PCI redaction (card numbers, SSN, phone
numbers, names, ...) is production-scope and belongs in the hardened build —
add entries to ``_PATTERNS`` to extend it.

Placeholder format is ``[scrubbed]@example.com`` per Task 5.5 AC #5. Using a
syntactically-valid email placeholder means the scrubbed record still parses
as an email address downstream (some LLM prompts / regex validators care).
"""

from __future__ import annotations

import re

from mail_ingestor.schemas import EmailMessage, SummaryRecord

EMAIL_PLACEHOLDER = "[scrubbed]@example.com"

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


def redact_email_message(message: EmailMessage) -> EmailMessage:
    """Return a copy of ``message`` with PII scrubbed from every text field
    that can carry an email address — the summarizer-prompt fields
    (subject, sender, body_text) AND the ancillary fields that would
    otherwise leak downstream (recipients, snippet).

    ``attachment_filenames`` is intentionally NOT scrubbed: the
    stub-grade email regex is greedy and would replace an entire
    ``invoice-alice@example.com.pdf`` with ``[scrubbed]@example.com``,
    losing the file extension and prefix. Filenames rarely carry
    email-shaped PII in normal usage; hand-inspect if this changes.

    ``message_id``, ``thread_id``, ``labels``, ``received_at`` carry no
    PII surface at PoC scope and are left intact. The model is frozen,
    so this returns a new instance via ``model_copy`` rather than
    mutating.

    This is INPUT-SIDE scrub (before the LLM sees the message). For
    OUTPUT-SIDE scrub of the persisted record (before ``demo/*.json``
    commit), use :func:`redact_summary_record`.
    """
    return message.model_copy(
        update={
            "subject": redact(message.subject),
            "sender": redact(message.sender),
            "recipients": [redact(r) for r in message.recipients],
            "body_text": redact(message.body_text),
            "snippet": redact(message.snippet) if message.snippet is not None else None,
        }
    )


def redact_summary_record(record: SummaryRecord) -> SummaryRecord:
    """Return a copy of ``record`` with PII scrubbed from every text field.

    OUTPUT-SIDE scrub for demo-artifact generation (Task 5.5 deliverable):
    a real live run produces a ``SummaryRecord`` whose ``subject`` and
    nested ``Summary.*`` text fields may echo email addresses from the
    source message. Before committing that record as ``demo/00N_sample.json``
    for the Ali Murat Journey 1 review, every string field is scrubbed.

    Provenance fields (``source_message_id``, ``model``, ``tokens_*``,
    ``created_at``) are left intact — they are not PII.
    """
    scrubbed_summary = record.summary.model_copy(
        update={
            "tl_dr": redact(record.summary.tl_dr),
            "summary": redact(record.summary.summary),
            "key_points": [redact(kp) for kp in record.summary.key_points],
            "action_items": [redact(ai) for ai in record.summary.action_items],
            "category": redact(record.summary.category),
        }
    )
    return record.model_copy(
        update={
            "subject": redact(record.subject),
            "summary": scrubbed_summary,
        }
    )
