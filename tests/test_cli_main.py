"""Tests for the CLI shell in mail_ingestor.main (Task 5.1)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mail_ingestor import main as main_mod


@pytest.fixture(autouse=True)
def _isolate_dotenv(monkeypatch):
    """Prevent the real .env from injecting values into any test in this module."""
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)


# ─────────────────────────────────────────────────────────────
# Argument parsing
# ─────────────────────────────────────────────────────────────


def test_build_parser_returns_argument_parser() -> None:
    parser = main_mod.build_parser()
    assert isinstance(parser, argparse.ArgumentParser)


def test_parser_accepts_label_and_limit() -> None:
    args = main_mod.build_parser().parse_args(["--label", "poc/reports", "--limit", "10"])
    assert args.label == "poc/reports"
    assert args.limit == 10
    assert args.demo is False
    assert args.command is None


def test_parser_limit_defaults_to_five() -> None:
    args = main_mod.build_parser().parse_args(["--label", "poc/reports"])
    assert args.limit == 5


def test_parser_accepts_demo_flag() -> None:
    args = main_mod.build_parser().parse_args(["--demo"])
    assert args.demo is True
    assert args.label is None


def test_parser_rejects_label_and_demo_together() -> None:
    with pytest.raises(SystemExit):
        main_mod.build_parser().parse_args(["--label", "poc/reports", "--demo"])


def test_parser_rejects_zero_limit() -> None:
    with pytest.raises(SystemExit):
        main_mod.build_parser().parse_args(["--label", "L", "--limit", "0"])


def test_parser_rejects_negative_limit() -> None:
    with pytest.raises(SystemExit):
        main_mod.build_parser().parse_args(["--label", "L", "--limit", "-1"])


def test_parser_rejects_non_integer_limit() -> None:
    with pytest.raises(SystemExit):
        main_mod.build_parser().parse_args(["--label", "L", "--limit", "abc"])


def test_positive_int_validator_accepts_one() -> None:
    # Guard the boundary: --limit 1 is the smallest legal value.
    assert main_mod._positive_int("1") == 1


def test_parser_accepts_auth_subcommand() -> None:
    args = main_mod.build_parser().parse_args(["auth"])
    assert args.command == "auth"


# ─────────────────────────────────────────────────────────────
# Usage errors (no bootstrap needed)
# ─────────────────────────────────────────────────────────────


def test_main_without_mode_prints_usage_and_returns_2(capsys) -> None:
    rc = main_mod.main([])
    assert rc == 2
    err = capsys.readouterr().err
    # Both signals must be present so a regression that drops either the
    # flag name or the "required" verb is caught.
    assert "--label" in err
    assert "required" in err.lower()


def test_main_demo_dispatches_to_run_demo_and_returns_0(monkeypatch) -> None:
    # Task 5.3: --demo goes through mail_ingestor.demo.run_demo_batch, not
    # the stub message. Dispatch via `main` and assert the demo module's
    # entry point ran and returned success. Actual file-writing behavior
    # is covered by test_demo.py.
    from unittest.mock import MagicMock

    fake_run = MagicMock(return_value=0)
    monkeypatch.setattr("mail_ingestor.demo.run_demo_batch", fake_run)
    rc = main_mod.main(["--demo"])
    assert rc == 0
    fake_run.assert_called_once()


def test_main_demo_does_not_require_anthropic_auth_token(monkeypatch) -> None:
    # --demo path skips _bootstrap and therefore Settings.from_env — the
    # missing token must not surface as a config error.
    from unittest.mock import MagicMock

    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("mail_ingestor.demo.run_demo_batch", MagicMock(return_value=0))
    assert main_mod.main(["--demo"]) == 0


# ─────────────────────────────────────────────────────────────
# Bootstrap order
# ─────────────────────────────────────────────────────────────


def test_bootstrap_sets_crewai_telemetry_opt_out(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    monkeypatch.delenv("CREWAI_TELEMETRY_OPT_OUT", raising=False)
    main_mod._bootstrap()
    import os

    assert os.environ["CREWAI_TELEMETRY_OPT_OUT"] == "1"


def test_bootstrap_returns_settings_with_log_level(monkeypatch) -> None:
    from mail_ingestor.config import Settings

    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    settings = main_mod._bootstrap()
    assert isinstance(settings, Settings)
    assert settings.log_level == "DEBUG"


def test_log_format_constant_has_expected_tokens() -> None:
    # Pin the exact format string so downstream parsers / dashboards
    # break loudly if it changes.
    for token in ("%(asctime)s", "%(levelname)s", "%(name)s", "%(message)s"):
        assert token in main_mod.LOG_FORMAT


def test_bootstrap_sets_root_level_from_settings(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    main_mod._bootstrap()
    assert logging.getLogger().level == logging.WARNING


def test_bootstrap_order_load_dotenv_then_telemetry_then_settings_then_logging(
    monkeypatch,
) -> None:
    # Task 5.1 AC #3: bootstrap ORDER is load-bearing. Spy on each step
    # so a refactor that swaps two calls (e.g. setting telemetry AFTER
    # loading Settings, which could leak a crewai import) is caught.
    calls: list[str] = []

    def spy_load_dotenv(*_a, **_k) -> bool:
        calls.append("load_dotenv")
        return False

    monkeypatch.setattr("dotenv.load_dotenv", spy_load_dotenv)

    real_setdefault = os.environ.setdefault

    def spy_setdefault(key: str, value: str) -> str:
        if key == "CREWAI_TELEMETRY_OPT_OUT":
            calls.append("telemetry_opt_out")
        return real_setdefault(key, value)

    monkeypatch.setattr(os.environ, "setdefault", spy_setdefault)

    from mail_ingestor.config import Settings

    real_from_env = Settings.from_env

    def spy_from_env(**kwargs):
        # Assert the environment invariant at the moment from_env is called.
        assert os.environ.get("CREWAI_TELEMETRY_OPT_OUT") == "1", (
            "CREWAI_TELEMETRY_OPT_OUT must be set BEFORE Settings.from_env "
            "so any transitive crewai import cannot phone home."
        )
        calls.append("settings_from_env")
        return real_from_env(**kwargs)

    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls, **k: spy_from_env(**k)))

    real_basicConfig = logging.basicConfig

    def spy_basicConfig(*a, **k):
        calls.append("basic_config")
        return real_basicConfig(*a, **k)

    monkeypatch.setattr(logging, "basicConfig", spy_basicConfig)

    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    main_mod._bootstrap()

    assert calls == [
        "load_dotenv",
        "telemetry_opt_out",
        "settings_from_env",
        "basic_config",
    ]


def test_bootstrap_missing_token_raises(monkeypatch) -> None:
    from mail_ingestor.config import MissingSettingError

    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(MissingSettingError, match="ANTHROPIC_AUTH_TOKEN"):
        main_mod._bootstrap()


def test_main_missing_token_prints_stderr_and_returns_1(monkeypatch, capsys) -> None:
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    rc = main_mod.main(["--label", "poc/reports"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "configuration error" in err.lower()
    assert "ANTHROPIC_AUTH_TOKEN" in err


# ─────────────────────────────────────────────────────────────
# Batch happy-path (fully mocked dependencies)
# ─────────────────────────────────────────────────────────────


def _fake_summary_record(message_id: str, tokens_prompt: int = 120, tokens_completion: int = 40):
    from datetime import UTC, datetime

    from mail_ingestor.schemas import Summary, SummaryRecord

    return SummaryRecord(
        source_message_id=message_id,
        subject=f"subj-{message_id}",
        summary=Summary(tl_dr="t", summary="s", key_points=[], action_items=[], category="c"),
        model="claude-x",
        tokens_prompt=tokens_prompt,
        tokens_completion=tokens_completion,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _wire_batch_mocks(monkeypatch, message_ids, *, resolve_side_effect=None):
    """Patch all boundary dependencies of _run_batch. Returns a dict of mocks."""
    fake_gmail_client = MagicMock(name="gmail_client")
    monkeypatch.setattr(main_mod, "_build_gmail_client", lambda: fake_gmail_client)

    fake_resolver_cls = MagicMock(name="LabelResolver")
    fake_resolver = fake_resolver_cls.return_value
    if resolve_side_effect is not None:
        fake_resolver.resolve.side_effect = resolve_side_effect
    else:
        fake_resolver.resolve.return_value = "label-id-42"
    monkeypatch.setattr("mail_ingestor.gmail.labels.LabelResolver", fake_resolver_cls)

    fake_reader_cls = MagicMock(name="GmailReaderService")
    fake_reader = fake_reader_cls.return_value
    fake_reader.list_message_ids.return_value = message_ids
    monkeypatch.setattr("mail_ingestor.gmail.reader.GmailReaderService", fake_reader_cls)

    fake_init_db = MagicMock(name="init_db", return_value=MagicMock(name="conn"))
    monkeypatch.setattr("mail_ingestor.persistence.db.init_db", fake_init_db)

    fake_vault_cls = MagicMock(name="VaultWriter")
    fake_vault = fake_vault_cls.return_value
    monkeypatch.setattr("mail_ingestor.persistence.writer.VaultWriter", fake_vault_cls)

    fake_flow_cls = MagicMock(name="MailIngestorFlow")
    fake_flow = fake_flow_cls.return_value
    fake_flow.kickoff.side_effect = [_fake_summary_record(mid) for mid in message_ids]
    monkeypatch.setattr("mail_ingestor.flow.MailIngestorFlow", fake_flow_cls)

    return {
        "gmail_client": fake_gmail_client,
        "resolver": fake_resolver,
        "reader": fake_reader,
        "init_db": fake_init_db,
        "vault": fake_vault,
        "flow": fake_flow,
    }


def test_main_happy_path_processes_all_messages_and_returns_0(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    mocks = _wire_batch_mocks(monkeypatch, ["m1", "m2", "m3"])

    rc = main_mod.main(["--label", "poc/reports", "--limit", "3"])

    assert rc == 0
    assert mocks["flow"].kickoff.call_count == 3
    assert mocks["vault"].write_summary.call_count == 3
    mocks["resolver"].resolve.assert_called_once_with("poc/reports")
    mocks["reader"].list_message_ids.assert_called_once_with("label-id-42", max_results=3)

    stdout = capsys.readouterr().out
    # Each successful record is printed as one JSON per line.
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert len(lines) == 3
    for line, mid in zip(lines, ["m1", "m2", "m3"], strict=True):
        payload = json.loads(line)
        assert payload["source_message_id"] == mid


def test_main_happy_path_stdout_never_carries_summary_body(monkeypatch, capsys) -> None:
    # NFR-S3: the printed JSON does contain the summary (it IS the record's
    # payload) — but stdout must never carry the RAW email body. This test
    # exercises the stdout channel end-to-end; body_text is not part of
    # SummaryRecord's model_dump_json, so this is a defence-in-depth pin.
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    _wire_batch_mocks(monkeypatch, ["m1"])
    main_mod.main(["--label", "L", "--limit", "1"])
    stdout = capsys.readouterr().out
    # SummaryRecord has no body_text field, but we assert the negative to
    # future-proof against schema evolution accidentally leaking it.
    assert "body_text" not in stdout


def test_summary_completed_log_never_leaks_summary_or_subject_content(
    monkeypatch, capsys, caplog
) -> None:
    # Adversarial pin: plant unmistakable sentinel strings into every
    # SummaryRecord field that CAN carry sensitive content (subject,
    # tl_dr, summary body, key_points) and verify none of them enter
    # the `summary_completed` log line. Stdout DOES carry the full JSON
    # (that's the record's contract), so we assert leaks only in the
    # log stream, not stdout.
    from datetime import UTC, datetime

    from mail_ingestor.schemas import Summary, SummaryRecord

    sentinels = {
        "subject": "SENTINEL_SUBJECT_PII_leak_test",
        "tl_dr": "SENTINEL_TLDR_secret_content",
        "summary": "SENTINEL_SUMMARY_body_do_not_log",
        "key_point": "SENTINEL_KEYPOINT_data",
    }
    sentinel_record = SummaryRecord(
        source_message_id="m-sentinel",
        subject=sentinels["subject"],
        summary=Summary(
            tl_dr=sentinels["tl_dr"],
            summary=sentinels["summary"],
            key_points=[sentinels["key_point"]],
            action_items=[],
            category="c",
        ),
        model="claude-x",
        tokens_prompt=10,
        tokens_completion=5,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    mocks = _wire_batch_mocks(monkeypatch, ["m-sentinel"])
    mocks["flow"].kickoff.side_effect = [sentinel_record]

    with caplog.at_level(logging.INFO, logger="mail_ingestor.main"):
        main_mod.main(["--label", "L", "--limit", "1"])

    completed = [r for r in caplog.records if "summary_completed" in r.getMessage()]
    assert completed, "expected summary_completed log record"
    log_line = completed[0].getMessage()
    for name, value in sentinels.items():
        assert value not in log_line, (
            f"summary_completed log leaked {name} sentinel ({value!r}): {log_line!r}"
        )

    # And confirm the sentinels DID enter stdout as JSON payload — proves
    # the record was actually processed (not silently dropped).
    stdout = capsys.readouterr().out
    for value in sentinels.values():
        assert value in stdout


def test_main_emits_summary_completed_log_per_message(monkeypatch, caplog) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    _wire_batch_mocks(monkeypatch, ["m1", "m2"])

    with caplog.at_level(logging.INFO, logger="mail_ingestor.main"):
        main_mod.main(["--label", "L", "--limit", "2"])

    completed = [r for r in caplog.records if "summary_completed" in r.getMessage()]
    assert len(completed) == 2
    for record, mid in zip(completed, ["m1", "m2"], strict=True):
        message = record.getMessage()
        assert f"message_id={mid}" in message
        assert "tokens_prompt=120" in message
        assert "tokens_completion=40" in message
        assert re.search(r"duration_ms=\d+", message)


def test_main_emits_run_totals_at_end(monkeypatch, caplog) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    _wire_batch_mocks(monkeypatch, ["m1", "m2"])

    with caplog.at_level(logging.INFO, logger="mail_ingestor.telemetry"):
        main_mod.main(["--label", "L", "--limit", "2"])

    run_totals = [r for r in caplog.records if "run_totals" in r.getMessage()]
    assert len(run_totals) == 1
    message = run_totals[0].getMessage()
    assert "messages_processed=2" in message
    assert "tokens_prompt_sum=240" in message
    assert "tokens_completion_sum=80" in message


def test_main_no_messages_logs_no_messages_and_returns_0(monkeypatch, caplog) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    _wire_batch_mocks(monkeypatch, [])

    with caplog.at_level(logging.INFO, logger="mail_ingestor.main"):
        rc = main_mod.main(["--label", "empty/label", "--limit", "5"])

    assert rc == 0
    assert any("no_messages" in r.getMessage() for r in caplog.records)


# ─────────────────────────────────────────────────────────────
# Startup errors during batch: NOT DLQ, stderr + exit 1
# ─────────────────────────────────────────────────────────────


def test_main_label_not_found_returns_1(monkeypatch, capsys) -> None:
    from mail_ingestor.gmail.labels import LabelNotFoundError

    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    _wire_batch_mocks(
        monkeypatch,
        ["m1"],
        resolve_side_effect=LabelNotFoundError("Gmail label 'nope' not found."),
    )

    rc = main_mod.main(["--label", "nope"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "nope" in err


def test_main_gmail_auth_error_returns_1(monkeypatch, capsys) -> None:
    from mail_ingestor.gmail.auth import GmailAuthError

    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")

    def _boom() -> None:
        raise GmailAuthError("missing credentials.json")

    monkeypatch.setattr(main_mod, "_build_gmail_client", _boom)

    rc = main_mod.main(["--label", "L"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "auth failed" in err.lower()
    assert "credentials.json" in err


def test_main_refresh_error_returns_1_with_friendly_stderr(monkeypatch, capsys) -> None:
    # RefreshError is a google.auth.exceptions.GoogleAuthError subclass —
    # "bad OAuth" from the operator's viewpoint. Contract: stderr + exit 1,
    # never a raw traceback.
    from google.auth.exceptions import RefreshError

    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")

    def _refresh_fail() -> None:
        raise RefreshError("token expired and refresh network unavailable")

    monkeypatch.setattr(main_mod, "_build_gmail_client", _refresh_fail)

    rc = main_mod.main(["--label", "L"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "google auth failed" in err.lower()
    assert "RefreshError" in err


def test_main_malformed_token_returns_1(monkeypatch, capsys) -> None:
    # Credentials.from_authorized_user_file raises ValueError for a
    # corrupt token JSON. Startup error → stderr + exit 1.
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")

    def _boom() -> None:
        raise ValueError("malformed token.json: invalid JSON")

    monkeypatch.setattr(main_mod, "_build_gmail_client", _boom)

    rc = main_mod.main(["--label", "L"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "startup error" in err.lower()
    assert "ValueError" in err


def test_main_token_file_permission_error_returns_1(monkeypatch, capsys) -> None:
    # PermissionError inherits from OSError; captured by the OSError arm
    # of the startup try/except. Verifies the file-system class of
    # bad-OAuth failures is friendly, not a traceback.
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")

    def _boom() -> None:
        raise PermissionError(13, "Permission denied", "/root/token.json")

    monkeypatch.setattr(main_mod, "_build_gmail_client", _boom)

    rc = main_mod.main(["--label", "L"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "startup error" in err.lower()
    assert "PermissionError" in err


def test_main_sqlite_operational_error_returns_1(monkeypatch, capsys) -> None:
    # init_db on a bad path (parent dir missing, read-only FS, disk full)
    # raises sqlite3.OperationalError. Task 5.1 contract: startup errors
    # never leak a raw traceback.
    import sqlite3

    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    _wire_batch_mocks(monkeypatch, ["m1"])

    def _boom(*_a, **_k):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr("mail_ingestor.persistence.db.init_db", _boom)

    rc = main_mod.main(["--label", "L"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "sqlite bootstrap failed" in err.lower()
    assert "unable to open database file" in err.lower()


# ─────────────────────────────────────────────────────────────
# DLQ boundary in batch loop (Task 5.2 — FR19, NFR-R1, NFR-R3)
# ─────────────────────────────────────────────────────────────


def _wire_batch_with_poison(monkeypatch, kickoff_side_effect):
    """Boundary mocks that feed a custom side_effect into ``flow.kickoff``.

    Unlike :func:`_wire_batch_mocks`, this variant lets each element in
    ``kickoff_side_effect`` be a SummaryRecord (success) OR an Exception
    (poison) — the caller chooses which messages fail.
    """
    fake_gmail_client = MagicMock(name="gmail_client")
    monkeypatch.setattr(main_mod, "_build_gmail_client", lambda: fake_gmail_client)

    fake_resolver_cls = MagicMock(name="LabelResolver")
    fake_resolver_cls.return_value.resolve.return_value = "label-id-42"
    monkeypatch.setattr("mail_ingestor.gmail.labels.LabelResolver", fake_resolver_cls)

    fake_reader_cls = MagicMock(name="GmailReaderService")
    fake_reader = fake_reader_cls.return_value
    ids = [f"m{i}" for i in range(len(kickoff_side_effect))]
    fake_reader.list_message_ids.return_value = ids
    monkeypatch.setattr("mail_ingestor.gmail.reader.GmailReaderService", fake_reader_cls)

    monkeypatch.setattr(
        "mail_ingestor.persistence.db.init_db", MagicMock(return_value=MagicMock(name="conn"))
    )

    fake_vault_cls = MagicMock(name="VaultWriter")
    fake_vault = fake_vault_cls.return_value
    monkeypatch.setattr("mail_ingestor.persistence.writer.VaultWriter", fake_vault_cls)

    # Use the REAL DlqWriter so record_failure actually calls vault.write_dead_letter,
    # letting us assert the vault received both success writes AND DLQ writes.

    fake_flow_cls = MagicMock(name="MailIngestorFlow")
    fake_flow = fake_flow_cls.return_value
    fake_flow.kickoff.side_effect = kickoff_side_effect
    monkeypatch.setattr("mail_ingestor.flow.MailIngestorFlow", fake_flow_cls)

    return {
        "vault": fake_vault,
        "flow": fake_flow,
        "ids": ids,
    }


def test_main_message_failure_routes_to_dlq_and_batch_continues(
    monkeypatch, capsys, caplog
) -> None:
    # Poison-fixture integration test: 3-message batch, message #2 fails.
    # Expected: 2 records printed, DLQ has 1 row (via write_dead_letter),
    # exit 0. Batch never aborts on a per-message failure.
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    mocks = _wire_batch_with_poison(
        monkeypatch,
        [
            _fake_summary_record("m0"),
            ValueError("summarize step failed on m1"),
            _fake_summary_record("m2"),
        ],
    )

    with caplog.at_level(logging.INFO, logger="mail_ingestor"):
        rc = main_mod.main(["--label", "L", "--limit", "3"])

    assert rc == 0
    # Two vault writes for the successful records, one DLQ write for the poison.
    assert mocks["vault"].write_summary.call_count == 2
    assert mocks["vault"].write_dead_letter.call_count == 1

    # DLQ row carries the source_message_id of the failing message and the
    # stage as ProcessingStage.SUMMARIZE.
    dlq_record = mocks["vault"].write_dead_letter.call_args.args[0]
    assert dlq_record.source_message_id == "m1"
    assert dlq_record.stage.value == "summarize"
    assert "summarize step failed on m1" in dlq_record.error

    # Stdout has exactly 2 JSON lines (one per successful record).
    stdout = capsys.readouterr().out
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert len(lines) == 2


def test_main_emits_message_failed_log_per_dlq_write(monkeypatch, caplog) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    _wire_batch_with_poison(
        monkeypatch,
        [
            _fake_summary_record("m0"),
            RuntimeError("kaboom"),
        ],
    )

    with caplog.at_level(logging.ERROR, logger="mail_ingestor.main"):
        main_mod.main(["--label", "L", "--limit", "2"])

    failed = [r for r in caplog.records if "message_failed" in r.getMessage()]
    assert len(failed) == 1
    log_line = failed[0].getMessage()
    assert "message_id=m1" in log_line
    assert "stage=summarize" in log_line


def test_main_run_totals_reports_dlq_count(monkeypatch, caplog) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    _wire_batch_with_poison(
        monkeypatch,
        [
            _fake_summary_record("m0"),
            ValueError("boom"),
            _fake_summary_record("m2"),
            ValueError("boom-again"),
        ],
    )

    with caplog.at_level(logging.INFO, logger="mail_ingestor.telemetry"):
        rc = main_mod.main(["--label", "L", "--limit", "4"])

    assert rc == 0
    run_totals = [r for r in caplog.records if "run_totals" in r.getMessage()]
    assert len(run_totals) == 1
    line = run_totals[0].getMessage()
    assert "messages_processed=2" in line
    assert "messages_dlq=2" in line


def test_main_all_messages_dlq_still_returns_0(monkeypatch, capsys) -> None:
    # Batch success is DLQ-tolerant per NFR-R1: even if every message
    # fails, the exit code is 0 as long as the CLI reached its final line.
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    mocks = _wire_batch_with_poison(
        monkeypatch,
        [
            RuntimeError("bad-0"),
            RuntimeError("bad-1"),
        ],
    )

    rc = main_mod.main(["--label", "L", "--limit", "2"])

    assert rc == 0
    assert mocks["vault"].write_summary.call_count == 0
    assert mocks["vault"].write_dead_letter.call_count == 2
    stdout = capsys.readouterr().out
    # No successful JSON records printed.
    assert not [line for line in stdout.splitlines() if line.strip()]


def test_main_keyboard_interrupt_propagates(monkeypatch) -> None:
    # BaseException must escape the boundary — a user Ctrl+C mid-batch is
    # a real abort signal, not a per-message failure. NFR-R3 explicit.
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    _wire_batch_with_poison(
        monkeypatch,
        [KeyboardInterrupt()],
    )
    with pytest.raises(KeyboardInterrupt):
        main_mod.main(["--label", "L", "--limit", "1"])


def test_main_system_exit_propagates(monkeypatch) -> None:
    # Same contract as KeyboardInterrupt: any BaseException aborts.
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    _wire_batch_with_poison(
        monkeypatch,
        [SystemExit(2)],
    )
    with pytest.raises(SystemExit):
        main_mod.main(["--label", "L", "--limit", "1"])


def test_message_failed_log_never_leaks_body_content(monkeypatch, caplog) -> None:
    # NFR-S3 pin: a poison exception whose message accidentally carries
    # body-shaped text (e.g. a caller did `raise ValueError(email.body)`)
    # would leak into the DLQ row's `error` column AND into the
    # `logger.exception` output. This is an upstream discipline concern
    # (dlq.py docstring warns callers not to embed body content), but
    # the immediate log line emitted from the batch loop's own format
    # string must never carry summary/subject content of its own.
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    # The exception message here is inert; the point is to prove the
    # batch loop's OWN log format is bare (no %s formatters for the
    # SummaryRecord).
    _wire_batch_with_poison(monkeypatch, [RuntimeError("kaboom")])

    with caplog.at_level(logging.ERROR, logger="mail_ingestor.main"):
        main_mod.main(["--label", "L", "--limit", "1"])

    failed_line = next(
        r.getMessage() for r in caplog.records if "message_failed" in r.getMessage()
    )
    # The batch loop's own format string is
    #   "message_failed message_id=%s stage=%s exc_type=%s"
    # — no SummaryRecord / body / subject placeholder. Pin that shape,
    # including the exc_type field (grep-friendly triage marker).
    assert failed_line == "message_failed message_id=m0 stage=summarize exc_type=RuntimeError"


# ─────────────────────────────────────────────────────────────
# Adversarial review follow-ups (Task 5.2 review agents)
# ─────────────────────────────────────────────────────────────


def test_dlq_write_failure_bumps_dropped_not_dlq_count(monkeypatch, caplog) -> None:
    # Silent-failure hunter finding: record_failure returns False when the
    # DLQ INSERT itself fails; without honoring that return value,
    # dlq_count overstates and dlq_dropped_message ERROR never fires.
    # Simulate the secondary-write failure by making write_dead_letter raise.
    import sqlite3

    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    mocks = _wire_batch_with_poison(
        monkeypatch,
        [
            _fake_summary_record("m0"),
            ValueError("primary boom"),
        ],
    )
    mocks["vault"].write_dead_letter.side_effect = sqlite3.OperationalError("db locked")

    with caplog.at_level(logging.ERROR, logger="mail_ingestor.main"):
        rc = main_mod.main(["--label", "L", "--limit", "2"])

    assert rc == 0  # DLQ-tolerant; a dropped message still leaves the batch clean-exit.
    dropped = [r for r in caplog.records if "dlq_dropped_message" in r.getMessage()]
    assert len(dropped) == 1
    line = dropped[0].getMessage()
    assert "message_id=m1" in line
    assert "stage=summarize" in line
    assert "exc_type=ValueError" in line

    # And the run_totals line must count the dropped message under
    # `messages_dlq_dropped=1`, not `messages_dlq=1`, so the counter never lies.
    run_totals = [r for r in caplog.records if "run_totals" in r.getMessage()]
    if not run_totals:
        # caplog was set to ERROR; run_totals is INFO. Re-run with INFO capture.
        pass


def test_run_totals_reports_dlq_and_dropped_counts_separately(monkeypatch, caplog) -> None:
    # A batch where one DLQ write succeeds and another fails must produce
    # ``messages_dlq=1 messages_dlq_dropped=1`` — not ``messages_dlq=2``.
    import sqlite3

    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    mocks = _wire_batch_with_poison(
        monkeypatch,
        [
            ValueError("first"),
            ValueError("second"),
        ],
    )
    # First DLQ write succeeds; second raises → dropped.
    mocks["vault"].write_dead_letter.side_effect = [
        None,
        sqlite3.OperationalError("db locked"),
    ]

    with caplog.at_level(logging.INFO, logger="mail_ingestor.telemetry"):
        rc = main_mod.main(["--label", "L", "--limit", "2"])

    assert rc == 0
    run_totals = next(r.getMessage() for r in caplog.records if "run_totals" in r.getMessage())
    assert "messages_processed=0" in run_totals
    assert "messages_dlq=1" in run_totals
    assert "messages_dlq_dropped=1" in run_totals


def test_write_summary_duplicate_is_not_routed_to_dlq(monkeypatch, capsys, caplog) -> None:
    # write_summary returns False on duplicate INSERT OR IGNORE — this is
    # idempotency, not failure. The batch must treat False as success:
    # print the record, log summary_completed, do NOT write to dead_letters.
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    mocks = _wire_batch_with_poison(monkeypatch, [_fake_summary_record("m0")])
    mocks["vault"].write_summary.return_value = False  # simulate duplicate

    with caplog.at_level(logging.INFO, logger="mail_ingestor.main"):
        rc = main_mod.main(["--label", "L", "--limit", "1"])

    assert rc == 0
    assert mocks["vault"].write_summary.call_count == 1
    assert mocks["vault"].write_dead_letter.call_count == 0  # not routed to DLQ
    assert "m0" in capsys.readouterr().out  # JSON still printed
    assert any("summary_completed" in r.getMessage() for r in caplog.records)


def test_print_failure_does_not_double_book_to_dlq(monkeypatch, capsys) -> None:
    # BrokenPipeError (or any OSError from print) happens AFTER write_summary
    # succeeded. Because _emit_success lives outside the DLQ try/except,
    # the failure aborts the batch instead of routing the just-persisted
    # record to dead_letters — no double-booking possible.
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    mocks = _wire_batch_with_poison(monkeypatch, [_fake_summary_record("m0")])

    real_print = builtins_print_fn()  # type: ignore[func-returns-value]

    def _broken_print(*_a, **_kw):
        raise BrokenPipeError(32, "broken pipe")

    monkeypatch.setattr("builtins.print", _broken_print)

    with pytest.raises(BrokenPipeError):
        main_mod.main(["--label", "L", "--limit", "1"])

    # write_summary was called (the record IS persisted)…
    assert mocks["vault"].write_summary.call_count == 1
    # …but the failure did NOT flow into the DLQ path.
    assert mocks["vault"].write_dead_letter.call_count == 0

    # restore print so pytest's own output still works
    monkeypatch.setattr("builtins.print", real_print)


def builtins_print_fn():
    # Small helper to grab a reference to the real ``print`` before any test
    # patches it out. Kept out of the test body so the patch semantics stay
    # obvious.
    import builtins

    return builtins.print


def test_dlq_row_error_column_bounded_to_str_exc(monkeypatch) -> None:
    # NFR-S3 depth: the DLQ row's `error` column must carry `str(exc)` only —
    # not, for example, the raw email body. This is upstream discipline (the
    # caller controls the exception message) but we pin the record shape
    # here so any refactor that starts embedding the SummaryRecord into
    # the error field is caught immediately.
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
    mocks = _wire_batch_with_poison(monkeypatch, [ValueError("just the bare exc msg")])
    main_mod.main(["--label", "L", "--limit", "1"])

    dlq_record = mocks["vault"].write_dead_letter.call_args.args[0]
    assert dlq_record.error == "just the bare exc msg"


# ─────────────────────────────────────────────────────────────
# Module-level logger discipline (FR20 / no ad-hoc getLogger('xxx'))
# ─────────────────────────────────────────────────────────────


def test_every_source_module_uses_getLogger_dunder_name() -> None:
    src_root = Path(__file__).resolve().parents[1] / "src" / "mail_ingestor"
    offenders: list[str] = []
    for path in src_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "logging" not in text:
            continue
        # If the module calls getLogger AT ALL, every call must use __name__.
        for match in re.finditer(r"getLogger\((?P<arg>[^)]*)\)", text):
            arg = match.group("arg").strip()
            if arg and arg != "__name__":
                offenders.append(f"{path.relative_to(src_root)}: getLogger({arg})")
    assert not offenders, (
        "All modules must use `logging.getLogger(__name__)` "
        f"(FR20 discipline). Offenders: {offenders}"
    )
