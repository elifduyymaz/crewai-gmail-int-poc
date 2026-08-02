import json
from datetime import UTC, datetime

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
