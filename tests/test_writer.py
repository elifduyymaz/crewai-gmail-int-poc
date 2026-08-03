import json
import logging
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from mail_ingestor.persistence.db import init_db
from mail_ingestor.persistence.writer import VaultWriter
from mail_ingestor.schemas import (
    DeadLetterRecord,
    ProcessingStage,
    Summary,
    SummaryRecord,
)

AWARE = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _summary_record(source_message_id: str = "m1", **over) -> SummaryRecord:
    summary = Summary(
        tl_dr="tldr",
        summary="the summary body",
        key_points=["k1", "k2"],
        action_items=["a1"],
        category="report",
    )
    base = {
        "source_message_id": source_message_id,
        "subject": "Subj",
        "summary": summary,
        "model": "claude-sonnet-5",
        "created_at": AWARE,
    }
    base.update(over)
    return SummaryRecord(**base)


def _writer() -> VaultWriter:
    return VaultWriter(init_db(":memory:"))


def test_write_summary_inserts_and_returns_true():
    writer = _writer()
    assert writer.write_summary(_summary_record()) is True
    rows = writer._conn.execute("SELECT * FROM summary_records").fetchall()
    assert len(rows) == 1


def test_write_summary_persists_flattened_fields():
    writer = _writer()
    writer.write_summary(_summary_record())
    row = writer._conn.execute(
        "SELECT source_message_id, subject, tl_dr, summary, category, model, created_at "
        "FROM summary_records"
    ).fetchone()
    assert row["source_message_id"] == "m1"
    assert row["subject"] == "Subj"
    assert row["tl_dr"] == "tldr"
    assert row["summary"] == "the summary body"
    assert row["category"] == "report"
    assert row["model"] == "claude-sonnet-5"
    assert row["created_at"] == AWARE.isoformat()


def test_write_summary_persists_token_totals_when_present():
    writer = _writer()
    writer.write_summary(_summary_record(tokens_prompt=1234, tokens_completion=567))
    row = writer._conn.execute(
        "SELECT tokens_prompt, tokens_completion FROM summary_records"
    ).fetchone()
    assert row["tokens_prompt"] == 1234
    assert row["tokens_completion"] == 567


def test_write_summary_persists_null_tokens_when_absent():
    writer = _writer()
    writer.write_summary(_summary_record())  # no tokens_prompt / tokens_completion
    row = writer._conn.execute(
        "SELECT tokens_prompt, tokens_completion FROM summary_records"
    ).fetchone()
    assert row["tokens_prompt"] is None
    assert row["tokens_completion"] is None


def test_write_summary_json_encodes_lists():
    writer = _writer()
    writer.write_summary(_summary_record())
    row = writer._conn.execute("SELECT key_points, action_items FROM summary_records").fetchone()
    assert json.loads(row["key_points"]) == ["k1", "k2"]
    assert json.loads(row["action_items"]) == ["a1"]


def test_write_summary_duplicate_is_ignored():
    writer = _writer()
    assert writer.write_summary(_summary_record("dup")) is True
    assert writer.write_summary(_summary_record("dup", subject="Changed")) is False
    rows = writer._conn.execute(
        "SELECT subject FROM summary_records WHERE source_message_id = 'dup'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["subject"] == "Subj"  # original kept, not overwritten


def test_write_summary_duplicate_emits_debug_log_without_body(caplog):
    writer = _writer()
    writer.write_summary(_summary_record("dup"))
    with caplog.at_level(logging.DEBUG, logger="mail_ingestor.persistence.writer"):
        writer.write_summary(_summary_record("dup"))
    matching = [r for r in caplog.records if "summary_skipped_duplicate" in r.getMessage()]
    assert matching, "expected a summary_skipped_duplicate debug record"
    message = matching[0].getMessage()
    assert "source_message_id=dup" in message
    assert "the summary body" not in message  # never leak summary content
    assert "tldr" not in message


def test_double_run_five_messages_yields_five_rows_not_ten():
    # Empirical probe for CrewAI Issue #5802 at PoC scale: replaying the same
    # 5-message batch must not double the vault row count. See findings.md.
    writer = _writer()
    ids = [f"msg-{i}" for i in range(5)]
    for message_id in ids:
        assert writer.write_summary(_summary_record(message_id)) is True

    for message_id in ids:
        assert writer.write_summary(_summary_record(message_id)) is False

    count = writer._conn.execute("SELECT COUNT(*) FROM summary_records").fetchone()[0]
    assert count == 5


def test_write_dead_letter_inserts_and_returns_rowid():
    writer = _writer()
    rowid = writer.write_dead_letter(
        DeadLetterRecord(
            source_message_id="m9",
            stage=ProcessingStage.SUMMARIZE,
            error="boom",
            failed_at=AWARE,
        )
    )
    assert isinstance(rowid, int)
    row = writer._conn.execute("SELECT * FROM dead_letters").fetchone()
    assert row["source_message_id"] == "m9"
    assert row["stage"] == "summarize"
    assert row["error"] == "boom"
    assert row["failed_at"] == AWARE.isoformat()


def test_write_dead_letter_persists_traceback():
    writer = _writer()
    tb = 'Traceback (most recent call last):\n  File "x", line 1, in y\nValueError: boom\n'
    writer.write_dead_letter(
        DeadLetterRecord(
            source_message_id="m9",
            stage=ProcessingStage.PARSE,
            error="ValueError: boom",
            traceback=tb,
            failed_at=AWARE,
        )
    )
    row = writer._conn.execute("SELECT traceback FROM dead_letters").fetchone()
    assert row["traceback"] == tb


def test_write_dead_letter_traceback_omitted_defaults_empty():
    writer = _writer()
    writer.write_dead_letter(
        DeadLetterRecord(stage=ProcessingStage.READ, error="e", failed_at=AWARE)
    )
    row = writer._conn.execute("SELECT traceback FROM dead_letters").fetchone()
    assert row["traceback"] == ""


def test_write_dead_letter_allows_duplicates_and_null_source():
    writer = _writer()
    writer.write_dead_letter(
        DeadLetterRecord(stage=ProcessingStage.READ, error="e1", failed_at=AWARE)
    )
    writer.write_dead_letter(
        DeadLetterRecord(stage=ProcessingStage.READ, error="e2", failed_at=AWARE)
    )
    rows = writer._conn.execute("SELECT source_message_id FROM dead_letters").fetchall()
    assert len(rows) == 2
    assert all(r["source_message_id"] is None for r in rows)


def test_write_dead_letter_raises_when_driver_returns_no_rowid():
    # Guards writer.py's `rowid is None` branch: a plain INSERT that reports
    # no lastrowid is a driver-level anomaly that must fail loud so the
    # outer DlqWriter contract logs it, not be masked by int(None).
    fake_cursor = MagicMock()
    fake_cursor.lastrowid = None
    fake_conn = MagicMock()
    fake_conn.execute.return_value = fake_cursor
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.__exit__.return_value = False
    writer = VaultWriter(fake_conn)
    with pytest.raises(RuntimeError, match="dead_letters INSERT returned no rowid"):
        writer.write_dead_letter(
            DeadLetterRecord(stage=ProcessingStage.READ, error="e", failed_at=AWARE)
        )
