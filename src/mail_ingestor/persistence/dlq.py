"""Dead-letter queue writer with an isolation contract.

Framework-agnostic core — no CrewAI dependency. Records processing failures into
the ``dead_letters`` table via :class:`VaultWriter`, under an isolation contract:

- Each failure is written in its own transaction (VaultWriter's ``with conn:``),
  decoupled from success-path writes.
- ``record_failure`` is BEST-EFFORT: it never raises. If writing the dead letter
  itself fails, both the *primary* failure (the one we tried to record) and the
  *secondary* failure (why the DB write blew up) are logged, then ``False`` is
  returned — recording a failure must never break the caller, but oncall must
  still be able to root-cause the primary event from logs alone.
- ``capture`` isolates a per-message processing block: on an ``Exception`` it
  records a dead letter and swallows it, so one poison message cannot break the
  batch. ``BaseException`` (KeyboardInterrupt, SystemExit) still propagates.
  If the DLQ write itself failed inside ``capture``, an ``ERROR`` log line
  ``dlq_capture_dropped_message`` is emitted so a silently lost message still
  leaves a durable signal in the log stream.
- Both entry points capture ``traceback.format_exc()`` while the exception
  frame is still live, so the persisted DLQ row carries a full stack trace
  even when only an exception object (and not the raise site) is available.

Callers must NOT embed message body content in raised exception messages —
the exception ``str()`` lands in the persisted ``error`` column and in the
log line for the secondary failure path, so a leaky ``__str__`` would leak
downstream (NFR-S3: never log body content).
"""

from __future__ import annotations

import logging
import sys
import traceback as _traceback
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
        tb: str | None = None,
    ) -> bool:
        """Record a processing failure. NEVER raises; returns True on success.

        When ``tb`` is None and the caller is inside an active ``except``
        block, the current traceback is captured automatically. Pass ``tb=""``
        to opt out; pass an explicit string to override.
        """
        # Resolve inputs *outside* the try so their formatting can't be lost
        # inside the secondary-exception branch — we need them to reconstruct
        # the primary failure from logs if the DB write itself dies.
        resolved_tb = tb if tb is not None else self._active_traceback()
        error_text = self._error_text(error)
        try:
            record = DeadLetterRecord(
                source_message_id=source_message_id,
                stage=stage,
                error=error_text,
                traceback=resolved_tb,
            )
            self._writer.write_dead_letter(record)
        except Exception:
            # The DLQ write itself failed. Log the primary payload so oncall
            # can root-cause without the persisted row, then the secondary
            # stack via _logger.exception (uses the active exc_info).
            _logger.warning(
                "dlq_write_failed source_message_id=%r stage=%s primary_error=%r",
                source_message_id,
                stage.value,
                error_text,
            )
            if resolved_tb:
                _logger.warning("dlq_primary_traceback %s", resolved_tb)
            _logger.exception(
                "dlq_secondary_exception source_message_id=%r stage=%s",
                source_message_id,
                stage.value,
            )
            return False
        return True

    @contextmanager
    def capture(
        self, stage: ProcessingStage, *, source_message_id: str | None = None
    ) -> Iterator[None]:
        """Isolate a processing block: on Exception, record to the DLQ and swallow it.

        When ``record_failure`` reports a secondary DLQ-write failure
        (``False``), emit an ERROR log so the caller can distinguish
        "message dead-lettered" from "message silently dropped".
        """
        try:
            yield
        except Exception as exc:  # noqa: BLE001
            tb = _traceback.format_exc()
            recorded = self.record_failure(
                stage, exc, source_message_id=source_message_id, tb=tb
            )
            if not recorded:
                _logger.error(
                    "dlq_capture_dropped_message source_message_id=%r stage=%s",
                    source_message_id,
                    stage.value,
                )

    @staticmethod
    def _active_traceback() -> str:
        # ``traceback.format_exc()`` outside an active ``except`` returns the
        # noisy ``"NoneType: None\n"`` sentinel; check ``sys.exc_info()``
        # directly to avoid depending on the sentinel's exact spelling.
        if sys.exc_info()[1] is None:
            return ""
        return _traceback.format_exc()

    @staticmethod
    def _error_text(error: str | BaseException) -> str:
        """Coerce error to a non-empty string; survives pathological __str__."""
        try:
            text = str(error)
        except Exception:  # noqa: BLE001 - defence for pathological __str__
            text = ""
        if text:
            return text
        try:
            return repr(error)
        except Exception:  # noqa: BLE001 - defence for pathological __repr__
            return f"<{type(error).__name__}>"
