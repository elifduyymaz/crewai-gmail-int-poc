"""SQLite bootstrap and schema (DDL) for the ingestion PoC.

Framework-agnostic core — no CrewAI dependency. Creates the database file and
the tables holding SummaryRecord (success) and DeadLetterRecord (failure). The
Summary is stored flattened (scalar columns; ``key_points``/``action_items`` as
JSON text). The insert/read writer lands in a later task.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("mail_ingestor.db")

SUMMARY_RECORDS_DDL = """
CREATE TABLE IF NOT EXISTS summary_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_message_id TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    tl_dr TEXT NOT NULL,
    summary TEXT NOT NULL,
    key_points TEXT NOT NULL DEFAULT '[]',
    action_items TEXT NOT NULL DEFAULT '[]',
    category TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

SUMMARY_RECORDS_UNIQUE_INDEX_DDL = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_summary_records_source_message_id
    ON summary_records (source_message_id)
"""

DEAD_LETTERS_DDL = """
CREATE TABLE IF NOT EXISTS dead_letters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_message_id TEXT,
    stage TEXT NOT NULL,
    error TEXT NOT NULL,
    failed_at TEXT NOT NULL
)
"""

_DDL_STATEMENTS = (
    SUMMARY_RECORDS_DDL,
    SUMMARY_RECORDS_UNIQUE_INDEX_DDL,
    DEAD_LETTERS_DDL,
)


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) a SQLite connection with sane defaults."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def bootstrap(conn: sqlite3.Connection) -> None:
    """Create the tables and index if they don't already exist (idempotent)."""
    with conn:
        for statement in _DDL_STATEMENTS:
            conn.execute(statement)


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Connect and bootstrap the schema; return the ready connection."""
    conn = connect(db_path)
    bootstrap(conn)
    return conn
