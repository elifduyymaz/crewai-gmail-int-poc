"""Batch-level telemetry helpers for the ingestion pipeline.

Framework-agnostic core — no CrewAI dependency. Sums the per-record token
counts populated by ``flow.TokenUsage`` and emits a single structured
INFO log for cost analysis in ``findings.md``.

Sum semantics mirror the per-record accumulator: ``None`` means "no
record in the batch reported that metric" (all mocked / all missing);
if even one record had a count, the total is the sum of the known
counts. Callers that want a "strict-known-only" behavior can filter
records before invoking the helper.

The log line never carries subject / body / summary content — only
counts and integers — so it is safe to emit at any verbosity.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from mail_ingestor.schemas import SummaryRecord

logger = logging.getLogger(__name__)


def log_run_totals(records: Iterable[SummaryRecord]) -> None:
    """Emit ``run_totals messages_processed=N tokens_prompt_sum=X tokens_completion_sum=Y``.

    The sums are the best-effort accumulation of the records' per-message
    token totals. ``None`` sums propagate to the log line verbatim (as
    the literal string ``None``) so a reader can tell "usage was never
    observed" from "usage was observed and summed to 0".
    """
    processed = 0
    prompt_sum: int | None = None
    completion_sum: int | None = None
    for record in records:
        processed += 1
        if record.tokens_prompt is not None:
            prompt_sum = (prompt_sum or 0) + record.tokens_prompt
        if record.tokens_completion is not None:
            completion_sum = (completion_sum or 0) + record.tokens_completion
    logger.info(
        "run_totals messages_processed=%d tokens_prompt_sum=%s tokens_completion_sum=%s",
        processed,
        prompt_sum,
        completion_sum,
    )
