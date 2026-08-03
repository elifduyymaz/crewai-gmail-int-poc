"""Write boundary records into the SQLite vault.

Framework-agnostic core — no CrewAI dependency. Persists ``SummaryRecord``
idempotently (INSERT OR IGNORE on the unique ``source_message_id``) and
``DeadLetterRecord`` (plain INSERT with the captured traceback). The
Summary is stored flattened; its list fields are JSON-encoded; datetimes
are stored as ISO 8601 text.

Reliability contract (never re-raise, secondary-exception isolation) is
NOT here — it lives one layer up in ``dlq.py`` on top of this writer.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from mail_ingestor.schemas import DeadLetterRecord, SummaryRecord

logger = logging.getLogger(__name__)


class VaultWriter:
    """Persist boundary records into the SQLite vault via a live connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def write_summary(self, record: SummaryRecord) -> bool:
        """Insert a ``SummaryRecord`` idempotently.

        Returns True if a new row was inserted, False if a row with the same
        ``source_message_id`` already existed (INSERT OR IGNORE). A duplicate
        emits a ``summary_skipped_duplicate`` debug log with the message id
        only — never the summary body.
        """
        summary = record.summary
        with self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO summary_records "
                "(source_message_id, subject, tl_dr, summary, key_points, "
                "action_items, category, model, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.source_message_id,
                    record.subject,
                    summary.tl_dr,
                    summary.summary,
                    json.dumps(summary.key_points),
                    json.dumps(summary.action_items),
                    summary.category,
                    record.model,
                    record.created_at.isoformat(),
                ),
            )
        inserted = cursor.rowcount == 1
        if not inserted:
            logger.debug(
                "summary_skipped_duplicate source_message_id=%s",
                record.source_message_id,
            )
        return inserted

    def write_dead_letter(self, record: DeadLetterRecord) -> int:
        """Insert a ``DeadLetterRecord`` (always) and return the new row id."""
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO dead_letters "
                "(source_message_id, stage, error, traceback, failed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    record.source_message_id,
                    record.stage.value,
                    record.error,
                    record.traceback,
                    record.failed_at.isoformat(),
                ),
            )
        rowid = cursor.lastrowid
        if rowid is None:
            # Plain INSERT (no OR IGNORE) sets lastrowid on success; a None
            # here means the driver reported neither success nor a raised
            # error. Fail loud so DlqWriter's outer contract logs it rather
            # than silently returning a bogus id to the caller.
            raise RuntimeError("dead_letters INSERT returned no rowid")
        return rowid
