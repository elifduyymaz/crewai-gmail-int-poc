"""NFR-R1 / FR19: per-message DLQ isolation contract.

Task 5.4 AC #2. A poison message must land in ``dead_letters`` exactly
once; other messages in the batch continue through the pipeline; the
CLI exits 0 (batch success is DLQ-tolerant).

Two levels of coverage compose the AC:

1. :func:`test_dlq_isolation_end_to_end_real_parser_through_flow_to_dlq`
   drives the ``poison_bad_internal_date.json`` fixture through the
   REAL ``parse_gmail_message`` inside a REAL ``MailIngestorFlow`` —
   only the outer edges (Gmail service, LLM adapter) are mocked. This
   composes the parser → flow → boundary chain that the injection-based
   test below cannot exercise.
2. :func:`test_dlq_isolation_poison_lands_in_dlq_and_batch_continues`
   uses an injected ``ValueError`` via ``kickoff.side_effect`` — fast,
   focused on the boundary itself.

The fixture is named ``poison_bad_internal_date.json`` (Task 5.4 originally
called it ``poison_bad_internal_date.json`` but the payload has always been a
non-numeric ``internalDate``; renamed to remove the naming lie).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mail_ingestor import main as main_mod
from mail_ingestor.schemas import Summary, SummaryRecord

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "emails"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _canned_record(message_id: str) -> SummaryRecord:
    return SummaryRecord(
        source_message_id=message_id,
        subject=f"subj-{message_id}",
        summary=Summary(
            tl_dr="ok",
            summary="clean",
            key_points=[],
            action_items=[],
            category="report",
        ),
        model="claude-x",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.fixture
def _stubbed_env(monkeypatch):
    """Stub the env so the batch runs against a controlled, hermetic view.

    * ``dotenv.load_dotenv`` is patched to a MagicMock. On teardown the
      fixture asserts the patched function was actually invoked — a
      refactor that changed the import site (e.g., ``import dotenv;
      dotenv.load_dotenv()``) would silently bypass the monkeypatch and
      load a real ``.env``. The post-hoc assertion turns that into a
      loud test failure.
    * ``ANTHROPIC_AUTH_TOKEN`` is set to a placeholder so ``Settings``
      passes validation without a real credential.
    * ``SQLITE_DB_PATH`` is deleted so no ambient env vars affect the
      in-memory SQLite the tests wire up.

    Note: this is a *stub* fixture, not runtime hermeticity enforcement.
    A test that opened a real network socket would still hit real
    infrastructure. That's a PoC-scope limitation — Story 5.6 may add
    a session-level ``socket.socket`` guard.
    """
    fake_load = MagicMock(return_value=False)
    monkeypatch.setattr("dotenv.load_dotenv", fake_load)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    yield
    assert fake_load.called, (
        "dotenv.load_dotenv was never invoked during the test — a refactor "
        "may have moved the import site and the stub no longer guarantees "
        "hermetic env. Update this fixture to patch the new site."
    )


def _wire_batch(monkeypatch, tmp_path, *, ids: list[str], kickoff_side_effect):
    """Boundary mocks: Gmail, DB, and MailIngestorFlow with a controlled
    kickoff side_effect list. Returns the fake vault so assertions can
    inspect write_summary / write_dead_letter call args.
    """
    fake_gmail = MagicMock(name="gmail_client")
    monkeypatch.setattr(main_mod, "_build_gmail_client", lambda: fake_gmail)

    fake_resolver_cls = MagicMock(name="LabelResolver")
    fake_resolver_cls.return_value.resolve.return_value = "label-id-42"
    monkeypatch.setattr("mail_ingestor.gmail.labels.LabelResolver", fake_resolver_cls)

    fake_reader_cls = MagicMock(name="GmailReaderService")
    fake_reader_cls.return_value.list_message_ids.return_value = ids
    monkeypatch.setattr("mail_ingestor.gmail.reader.GmailReaderService", fake_reader_cls)

    # Real in-memory SQLite — write_summary / write_dead_letter hit
    # actual rows we can query.
    real_conn = sqlite3.connect(":memory:")
    from mail_ingestor.persistence.db import bootstrap

    real_conn.row_factory = sqlite3.Row
    bootstrap(real_conn)
    monkeypatch.setattr("mail_ingestor.persistence.db.init_db", lambda _p: real_conn)

    fake_flow_cls = MagicMock(name="MailIngestorFlow")
    fake_flow_cls.return_value.kickoff.side_effect = kickoff_side_effect
    monkeypatch.setattr("mail_ingestor.flow.MailIngestorFlow", fake_flow_cls)

    return real_conn


def test_dlq_isolation_poison_lands_in_dlq_and_batch_continues(
    _stubbed_env, monkeypatch, tmp_path, capsys
) -> None:
    """Task 5.4 AC #2 core case: 3-message batch with one poison in the
    middle → 2 rows in summary_records, 1 row in dead_letters, exit 0.

    The poison side-effect here mimics what
    ``parse_gmail_message(poison_bad_internal_date.json)`` would raise inside
    ``flow.kickoff`` at the parse step — a ValueError from int() on the
    malformed internalDate. The DLQ boundary in main._run_batch is the
    real one, unmocked.
    """
    poison = _load("poison_bad_internal_date.json")
    assert poison["id"] == "poison-charset-001"

    conn = _wire_batch(
        monkeypatch,
        tmp_path,
        ids=["good-1", poison["id"], "good-2"],
        kickoff_side_effect=[
            _canned_record("good-1"),
            ValueError(
                "invalid literal for int() with base 10: 'not-a-numeric-timestamp'"
            ),
            _canned_record("good-2"),
        ],
    )

    rc = main_mod.main(["--label", "poc/reports", "--limit", "3"])

    assert rc == 0
    summary_rows = conn.execute(
        "SELECT source_message_id FROM summary_records ORDER BY source_message_id"
    ).fetchall()
    dlq_rows = conn.execute(
        "SELECT source_message_id, stage, error FROM dead_letters"
    ).fetchall()

    assert [r["source_message_id"] for r in summary_rows] == ["good-1", "good-2"]
    assert len(dlq_rows) == 1
    assert dlq_rows[0]["source_message_id"] == poison["id"]
    assert dlq_rows[0]["stage"] == "summarize"
    assert "not-a-numeric-timestamp" in dlq_rows[0]["error"]

    # Two successful JSON records on stdout — no poison payload.
    stdout_lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(stdout_lines) == 2
    for line, expected_id in zip(stdout_lines, ["good-1", "good-2"], strict=True):
        assert json.loads(line)["source_message_id"] == expected_id


def test_dlq_isolation_poison_charset_fixture_actually_raises_on_parse() -> None:
    """Prove the poison fixture would legitimately raise if fed through
    the real ``parse_gmail_message`` — not just via a mocked exception.
    Guards a regression that would silently accept malformed
    ``internalDate`` values (the very behavior we test the DLQ against).
    """
    from mail_ingestor.gmail.parser import parse_gmail_message

    poison = _load("poison_bad_internal_date.json")
    with pytest.raises(ValueError, match=r"invalid literal for int\(\)"):
        parse_gmail_message(poison)


def test_dlq_isolation_all_poison_batch_still_returns_0(
    _stubbed_env, monkeypatch, tmp_path, capsys
) -> None:
    """Contract: even if every message DLQs, exit code is 0 as long as
    the CLI reaches its final line. No successful JSON on stdout.
    """
    conn = _wire_batch(
        monkeypatch,
        tmp_path,
        ids=["p-1", "p-2"],
        kickoff_side_effect=[
            ValueError("first failure"),
            RuntimeError("second failure"),
        ],
    )

    rc = main_mod.main(["--label", "L"])

    assert rc == 0
    assert conn.execute("SELECT COUNT(*) FROM summary_records").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM dead_letters").fetchone()[0] == 2
    assert capsys.readouterr().out.strip() == ""


def test_dlq_isolation_end_to_end_real_parser_through_flow_to_dlq(
    _stubbed_env, monkeypatch, capsys
) -> None:
    """Compose parser + flow + DLQ boundary end-to-end.

    Mocks only the OUTER edges: Gmail auth/client (fake), LabelResolver
    (returns a canned label id), GmailReaderService (returns fixture
    payloads by id), NativeAnthropicLLM.call (returns canned Summary
    JSON). Everything between — parse, flow.summarize's CrewAI Task
    validation, vault write, DLQ boundary — runs real code.

    This closes the "injection vs real parse" gap. A future regression
    that makes ``parse_gmail_message`` silently return an empty
    ``EmailMessage`` for malformed input would land the poison in
    ``summary_records`` (or leave stdout with a phantom row) instead of
    ``dead_letters`` — this test would fail loudly.
    """
    from mail_ingestor.flow import NativeAnthropicLLM
    from mail_ingestor.persistence.db import bootstrap

    good_1 = _load("001_weekly_report.json")
    poison = _load("poison_bad_internal_date.json")
    good_2 = _load("002_meeting_recap.json")
    payloads = {good_1["id"]: good_1, poison["id"]: poison, good_2["id"]: good_2}

    fake_gmail = MagicMock(name="gmail_client")
    monkeypatch.setattr(main_mod, "_build_gmail_client", lambda: fake_gmail)

    fake_resolver_cls = MagicMock(name="LabelResolver")
    fake_resolver_cls.return_value.resolve.return_value = "label-real"
    monkeypatch.setattr("mail_ingestor.gmail.labels.LabelResolver", fake_resolver_cls)

    fake_reader_cls = MagicMock(name="GmailReaderService")
    fake_reader = fake_reader_cls.return_value
    fake_reader.list_message_ids.return_value = [good_1["id"], poison["id"], good_2["id"]]
    fake_reader.get_message.side_effect = lambda mid: payloads[mid]
    monkeypatch.setattr("mail_ingestor.gmail.reader.GmailReaderService", fake_reader_cls)

    real_conn = sqlite3.connect(":memory:")
    real_conn.row_factory = sqlite3.Row
    bootstrap(real_conn)
    monkeypatch.setattr("mail_ingestor.persistence.db.init_db", lambda _p: real_conn)

    # Mocked LLM returns the same canned Summary JSON for any prompt —
    # good messages produce valid SummaryRecords, the poison never
    # reaches the LLM step because parse raises first.
    canned = json.dumps(
        {
            "tl_dr": "end-to-end canned",
            "summary": "end-to-end canned body",
            "key_points": [],
            "action_items": [],
            "category": "test",
        }
    )
    monkeypatch.setattr(NativeAnthropicLLM, "call", lambda self, *a, **k: canned)

    rc = main_mod.main(["--label", "L", "--limit", "3"])

    assert rc == 0
    dlq_rows = real_conn.execute(
        "SELECT source_message_id, stage, error FROM dead_letters"
    ).fetchall()
    assert len(dlq_rows) == 1, "poison did not route through parser → flow → DLQ chain"
    assert dlq_rows[0]["source_message_id"] == poison["id"]
    assert dlq_rows[0]["stage"] == "summarize"
    # Real parse's ``ValueError: invalid literal for int()`` — proves the
    # parser (not an injected mock) is what tripped.
    assert "invalid literal for int" in dlq_rows[0]["error"]

    summary_ids = [
        r["source_message_id"]
        for r in real_conn.execute(
            "SELECT source_message_id FROM summary_records ORDER BY source_message_id"
        ).fetchall()
    ]
    assert summary_ids == [good_1["id"], good_2["id"]]


def test_dlq_isolation_run_totals_reports_dlq_count(
    _stubbed_env, monkeypatch, tmp_path, caplog
) -> None:
    """The batch-end run_totals line must reflect the split accurately —
    ``messages_processed=N`` counts only successes, ``messages_dlq=M``
    counts durable DLQ rows.
    """
    _wire_batch(
        monkeypatch,
        tmp_path,
        ids=["a", "b", "c"],
        kickoff_side_effect=[
            _canned_record("a"),
            ValueError("boom"),
            _canned_record("c"),
        ],
    )

    with caplog.at_level(logging.INFO, logger="mail_ingestor.telemetry"):
        main_mod.main(["--label", "L"])

    run_totals = next(r.getMessage() for r in caplog.records if "run_totals" in r.getMessage())
    assert "messages_processed=2" in run_totals
    assert "messages_dlq=1" in run_totals
    assert "messages_dlq_dropped=0" in run_totals
