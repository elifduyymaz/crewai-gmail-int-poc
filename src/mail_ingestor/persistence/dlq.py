"""Dead-letter queue writer with an isolation contract.

Framework-agnostic core — no CrewAI dependency. Records processing failures into
the ``dead_letters`` table via :class:`VaultWriter`, under an isolation contract:

- Each failure is written in its own transaction (VaultWriter's ``with conn:``),
  decoupled from success-path writes.
- ``record_failure`` is BEST-EFFORT: it never raises. If writing the dead letter
  itself fails, the error is logged and ``False`` is returned — recording a
  failure must never break the caller.
- ``capture`` isolates a per-message processing block: on an ``Exception`` it
  records a dead letter and swallows it, so one poison message cannot break the
  batch. ``BaseException`` (e.g. KeyboardInterrupt) still propagates.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from mail_ingestor.persistence.writer import VaultWriter
from mail_ingestor.schemas import DeadLetterRecord, ProcessingStage

_logger = logging.getLogger(__name__)


class DlqWriter:
    """Best-effort dead-letter sink with an isolation contract."""

    def __init__(self, writer: VaultWriter) -> None:
        self._writer = writer

    def record_failure(
        self,
        stage: ProcessingStage,
        error: str | BaseException,
        *,
        source_message_id: str | None = None,
    ) -> bool:
        """Record a processing failure. NEVER raises; returns True on success."""
        try:
            record = DeadLetterRecord(
                source_message_id=source_message_id,
                stage=stage,
                error=str(error) or repr(error),
            )
            self._writer.write_dead_letter(record)
        except Exception:
            _logger.exception(
                "DLQ write failed for message %s at stage %s", source_message_id, stage
            )
            return False
        return True

    @contextmanager
    def capture(
        self, stage: ProcessingStage, *, source_message_id: str | None = None
    ) -> Iterator[None]:
        """Isolate a processing block: on Exception, record to the DLQ and swallow it."""
        try:
            yield
        except Exception as exc:  # noqa: BLE001
            self.record_failure(stage, exc, source_message_id=source_message_id)
