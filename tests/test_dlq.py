import sqlite3
from unittest.mock import MagicMock

import pytest

from mail_ingestor.persistence.db import init_db
from mail_ingestor.persistence.dlq import DlqWriter
from mail_ingestor.persistence.writer import VaultWriter
from mail_ingestor.schemas import ProcessingStage


def _dlq() -> tuple[DlqWriter, sqlite3.Connection]:
    writer = VaultWriter(init_db(":memory:"))
    return DlqWriter(writer), writer._conn


def _dead_letters(conn: sqlite3.Connection) -> list:
    return conn.execute("SELECT * FROM dead_letters").fetchall()


def test_record_failure_writes_row_and_returns_true():
    dlq, conn = _dlq()
    assert dlq.record_failure(ProcessingStage.PARSE, "boom", source_message_id="m1") is True
    rows = _dead_letters(conn)
    assert len(rows) == 1
    assert rows[0]["stage"] == "parse"
    assert rows[0]["error"] == "boom"
    assert rows[0]["source_message_id"] == "m1"


def test_record_failure_accepts_exception_object():
    dlq, conn = _dlq()
    dlq.record_failure(ProcessingStage.SUMMARIZE, ValueError("bad thing"))
    assert _dead_letters(conn)[0]["error"] == "bad thing"


def test_record_failure_empty_error_is_still_recorded():
    dlq, conn = _dlq()
    assert dlq.record_failure(ProcessingStage.READ, "") is True
    assert _dead_letters(conn)[0]["error"]  # non-empty (repr fallback)


def test_record_failure_never_raises_on_write_error():
    bad = MagicMock(spec=VaultWriter)
    bad.write_dead_letter.side_effect = sqlite3.OperationalError("db is locked")
    dlq = DlqWriter(bad)
    assert dlq.record_failure(ProcessingStage.PERSIST, "boom") is False  # no raise


def test_capture_records_failure_and_swallows_exception():
    dlq, conn = _dlq()
    with dlq.capture(ProcessingStage.PARSE, source_message_id="m2"):
        raise ValueError("parse failed")
    rows = _dead_letters(conn)
    assert len(rows) == 1
    assert rows[0]["source_message_id"] == "m2"
    assert rows[0]["stage"] == "parse"
    assert "parse failed" in rows[0]["error"]


def test_capture_no_exception_writes_nothing():
    dlq, conn = _dlq()
    with dlq.capture(ProcessingStage.READ):
        pass
    assert _dead_letters(conn) == []


def test_capture_does_not_swallow_base_exception():
    dlq, conn = _dlq()
    with pytest.raises(KeyboardInterrupt), dlq.capture(ProcessingStage.READ):
        raise KeyboardInterrupt
    assert _dead_letters(conn) == []
