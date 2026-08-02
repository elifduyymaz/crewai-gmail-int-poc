"""Write boundary records into the SQLite vault.

Framework-agnostic core — no CrewAI dependency. Persists ``SummaryRecord``
idempotently (INSERT OR IGNORE on the unique ``source_message_id``) and
``DeadLetterRecord`` (plain INSERT). The Summary is stored flattened; its
list fields are JSON-encoded; datetimes are stored as ISO 8601 text.
"""

from __future__ import annotations

import json
import sqlite3

from mail_ingestor.schemas import DeadLetterRecord, SummaryRecord


class VaultWriter:
    """Persist boundary records into the SQLite vault via a live connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def write_summary(self, record: SummaryRecord) -> bool:
        """Insert a ``SummaryRecord`` idempotently.

        Returns True if a new row was inserted, False if a row with the same
        ``source_message_id`` already existed (INSERT OR IGNORE).
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
        return cursor.rowcount == 1

    def write_dead_letter(self, record: DeadLetterRecord) -> int:
        """Insert a ``DeadLetterRecord`` (always) and return the new row id."""
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO dead_letters (source_message_id, stage, error, failed_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    record.source_message_id,
                    record.stage.value,
                    record.error,
                    record.failed_at.isoformat(),
                ),
            )
        return int(cursor.lastrowid)
