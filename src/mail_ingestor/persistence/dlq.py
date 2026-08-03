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
- Both entry points capture ``traceback.format_exc()`` while the exception
  frame is still live, so the persisted DLQ row carries a full stack trace
  even when only an exception object (and not the raise site) is available.
"""

from __future__ import annotations

import logging
import traceback as _traceback
from collections.abc import Iterator
from contextlib import contextmanager

from mail_ingestor.persistence.writer import VaultWriter
from mail_ingestor.schemas import DeadLetterRecord, ProcessingStage

_logger = logging.getLogger(__name__)

# ``traceback.format_exc()`` outside an ``except`` block returns this sentinel
# rather than an empty string; treat it as "no live trace".
_NO_ACTIVE_TRACE = "NoneType: None\n"


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
        try:
            resolved_tb = tb if tb is not None else self._active_traceback()
            record = DeadLetterRecord(
                source_message_id=source_message_id,
                stage=stage,
                error=str(error) or repr(error),
                traceback=resolved_tb,
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
            tb = _traceback.format_exc()
            self.record_failure(stage, exc, source_message_id=source_message_id, tb=tb)

    @staticmethod
    def _active_traceback() -> str:
        tb = _traceback.format_exc()
        return "" if tb == _NO_ACTIVE_TRACE else tb
