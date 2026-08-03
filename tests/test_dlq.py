import logging
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


def test_record_failure_empty_string_error_falls_back_to_repr():
    # Pins the `str(error) or repr(error)` contract: "" is falsy so repr
    # kicks in; a future switch to a "unknown" sentinel would break this.
    dlq, conn = _dlq()
    assert dlq.record_failure(ProcessingStage.READ, "") is True
    assert _dead_letters(conn)[0]["error"] == repr("")


def test_record_failure_empty_exception_falls_back_to_repr():
    dlq, conn = _dlq()
    exc = ValueError()
    dlq.record_failure(ProcessingStage.READ, exc)
    assert _dead_letters(conn)[0]["error"] == repr(exc)


def test_record_failure_survives_pathological_str_dunder():
    # Exception whose __str__ raises must not break DLQ recording — the
    # error-text coercion has to be internally defensive.
    class BadStr(Exception):
        def __str__(self) -> str:
            raise RuntimeError("no str for you")

    dlq, conn = _dlq()
    result = dlq.record_failure(ProcessingStage.READ, BadStr())
    assert result is True
    stored = _dead_letters(conn)[0]["error"]
    assert stored  # non-empty via repr / class-name fallback


def test_record_failure_never_raises_on_write_error():
    bad = MagicMock(spec=VaultWriter)
    bad.write_dead_letter.side_effect = sqlite3.OperationalError("db is locked")
    dlq = DlqWriter(bad)
    assert dlq.record_failure(ProcessingStage.PERSIST, "boom") is False  # no raise


def test_record_failure_logs_primary_error_when_write_fails(caplog):
    # When the DLQ write itself dies, oncall must still be able to
    # root-cause the *primary* failure from logs alone. The secondary
    # traceback comes for free via logger.exception; the primary
    # error text and traceback must be explicitly logged.
    bad = MagicMock(spec=VaultWriter)
    bad.write_dead_letter.side_effect = sqlite3.OperationalError("db locked")
    dlq = DlqWriter(bad)
    with caplog.at_level(logging.DEBUG, logger="mail_ingestor.persistence.dlq"):
        try:
            raise RuntimeError("primary-kaboom")
        except RuntimeError as exc:
            result = dlq.record_failure(
                ProcessingStage.PERSIST, exc, source_message_id="m-primary"
            )
    assert result is False
    combined = " | ".join(r.getMessage() for r in caplog.records)
    assert "dlq_write_failed" in combined
    assert "primary_error=" in combined
    assert "primary-kaboom" in combined
    assert "'m-primary'" in combined
    assert "dlq_primary_traceback" in combined
    assert "dlq_secondary_exception" in combined


def test_capture_logs_error_when_record_failure_returns_false(caplog):
    # capture() must emit a loud signal when a message is silently dropped,
    # otherwise a DLQ-write failure erases the message from all observable
    # state (no row, no exception, no error log).
    bad = MagicMock(spec=VaultWriter)
    bad.write_dead_letter.side_effect = sqlite3.OperationalError("db locked")
    dlq = DlqWriter(bad)
    with (
        caplog.at_level(logging.ERROR, logger="mail_ingestor.persistence.dlq"),
        dlq.capture(ProcessingStage.PARSE, source_message_id="m-lost"),
    ):
        raise ValueError("primary")
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("dlq_capture_dropped_message" in r.getMessage() for r in error_records)
    assert any("'m-lost'" in r.getMessage() for r in error_records)


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


def test_capture_propagates_system_exit():
    # BaseException family — SystemExit must propagate untouched so a CLI
    # exit signal is not swallowed by the DLQ boundary.
    dlq, conn = _dlq()
    with pytest.raises(SystemExit), dlq.capture(ProcessingStage.READ):
        raise SystemExit(1)
    assert _dead_letters(conn) == []


def test_capture_persists_traceback():
    dlq, conn = _dlq()
    with dlq.capture(ProcessingStage.PARSE, source_message_id="m3"):
        raise ValueError("kaboom")
    row = _dead_letters(conn)[0]
    tb = row["traceback"]
    assert "Traceback" in tb
    assert "ValueError: kaboom" in tb
    assert "test_capture_persists_traceback" in tb


def test_record_failure_captures_traceback_from_active_except():
    dlq, conn = _dlq()
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        dlq.record_failure(ProcessingStage.SUMMARIZE, exc, source_message_id="m4")
    tb = _dead_letters(conn)[0]["traceback"]
    assert "Traceback" in tb
    assert "RuntimeError: boom" in tb


def test_record_failure_no_trace_when_called_outside_except():
    dlq, conn = _dlq()
    dlq.record_failure(ProcessingStage.PERSIST, "explicit string", source_message_id="m5")
    assert _dead_letters(conn)[0]["traceback"] == ""


def test_record_failure_explicit_tb_overrides_capture():
    dlq, conn = _dlq()
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        dlq.record_failure(ProcessingStage.READ, exc, tb="explicit-override")
    assert _dead_letters(conn)[0]["traceback"] == "explicit-override"


def test_record_failure_explicit_empty_tb_opts_out_of_capture():
    # Documented contract: `tb=""` explicitly disables auto-capture, even
    # when called from inside an active except frame.
    dlq, conn = _dlq()
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        dlq.record_failure(ProcessingStage.READ, exc, tb="")
    assert _dead_letters(conn)[0]["traceback"] == ""
