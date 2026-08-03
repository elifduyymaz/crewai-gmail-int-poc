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


def log_run_totals(
    records: Iterable[SummaryRecord],
    *,
    dead_letter_count: int = 0,
    dropped_count: int = 0,
) -> None:
    """Emit the end-of-batch summary line.

    Format::

        run_totals messages_processed=N messages_dlq=M messages_dlq_dropped=D
                   tokens_prompt_sum=X tokens_completion_sum=Y

    * ``messages_processed`` counts records that reached the vault.
    * ``messages_dlq`` counts failures that landed a durable row in
      ``dead_letters`` (``DlqWriter.record_failure`` returned True).
    * ``messages_dlq_dropped`` counts failures whose DLQ write itself
      failed (secondary-exception isolation fired) — these messages
      are TRULY lost and oncall must reconcile from the ERROR log
      stream. The counter exists so ``run_totals`` never overstates
      the DLQ tally.
    * Token sums are the best-effort accumulation of the records'
      per-message totals; ``None`` propagates verbatim so a reader can
      tell "usage was never observed" from "usage was observed and
      summed to 0".
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
        "run_totals messages_processed=%d messages_dlq=%d messages_dlq_dropped=%d "
        "tokens_prompt_sum=%s tokens_completion_sum=%s",
        processed,
        dead_letter_count,
        dropped_count,
        prompt_sum,
        completion_sum,
    )
