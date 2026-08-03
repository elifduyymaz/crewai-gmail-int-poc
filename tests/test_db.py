import sqlite3
from pathlib import Path

import pytest

from mail_ingestor.persistence.db import DEFAULT_DB_PATH, connect, init_db


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r[0] for r in rows}


def _insert_summary(conn: sqlite3.Connection, source_message_id: str) -> None:
    conn.execute(
        "INSERT INTO summary_records "
        "(source_message_id, tl_dr, summary, category, model, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (source_message_id, "t", "s", "c", "model-x", "2026-01-01T00:00:00+00:00"),
    )


def test_default_db_path_constant():
    assert DEFAULT_DB_PATH == Path("mail_ingestor.db")


def test_init_db_creates_the_file(tmp_path):
    db = tmp_path / "poc.db"
    assert not db.exists()
    conn = init_db(db)
    conn.close()
    assert db.exists()


def test_bootstrap_creates_both_tables():
    conn = init_db(":memory:")
    assert {"summary_records", "dead_letters"} <= _table_names(conn)


def test_summary_records_has_expected_columns():
    conn = init_db(":memory:")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(summary_records)")}
    assert {
        "id",
        "source_message_id",
        "subject",
        "tl_dr",
        "summary",
        "key_points",
        "action_items",
        "category",
        "model",
        "tokens_prompt",
        "tokens_completion",
        "created_at",
    } <= cols


def test_summary_records_token_columns_are_nullable():
    # tokens_prompt / tokens_completion mirror SummaryRecord's Optional[int]
    # semantics (None = "no usage observed"); a partial INSERT that omits
    # them must succeed rather than raise NOT NULL.
    conn = init_db(":memory:")
    conn.execute(
        "INSERT INTO summary_records "
        "(source_message_id, tl_dr, summary, category, model, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("m1", "t", "s", "c", "model-x", "2026-01-01T00:00:00+00:00"),
    )
    row = conn.execute(
        "SELECT tokens_prompt, tokens_completion FROM summary_records"
    ).fetchone()
    assert row["tokens_prompt"] is None
    assert row["tokens_completion"] is None


def test_dead_letters_has_expected_columns():
    conn = init_db(":memory:")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(dead_letters)")}
    assert {
        "id",
        "source_message_id",
        "stage",
        "error",
        "traceback",
        "failed_at",
    } <= cols


def test_dead_letters_traceback_defaults_to_empty_string():
    # DlqWriter always passes a captured traceback, but INSERTs that omit
    # the column still succeed (NOT NULL DEFAULT '') so a partial insert
    # can never wedge the batch.
    conn = init_db(":memory:")
    conn.execute(
        "INSERT INTO dead_letters (source_message_id, stage, error, failed_at) "
        "VALUES (?, ?, ?, ?)",
        ("m1", "read", "e", "2026-01-01T00:00:00+00:00"),
    )
    row = conn.execute("SELECT traceback FROM dead_letters").fetchone()
    assert row["traceback"] == ""


def test_unique_source_message_id_rejects_duplicate():
    conn = init_db(":memory:")
    _insert_summary(conn, "m1")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_summary(conn, "m1")


def test_bootstrap_is_idempotent(tmp_path):
    db = tmp_path / "idem.db"
    init_db(db).close()
    conn = init_db(db)  # second run must not raise
    assert {"summary_records", "dead_letters"} <= _table_names(conn)
    conn.close()


def test_connect_uses_row_factory():
    conn = connect(":memory:")
    assert conn.row_factory is sqlite3.Row
