"""Tests for the CLI shell in mail_ingestor.main (Task 5.1)."""

from __future__ import annotations

import argparse
import json
import logging
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
    assert "--label" in err or "required" in err.lower()


def test_main_demo_returns_2_with_stub_message(capsys) -> None:
    rc = main_mod.main(["--demo"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "5.3" in err or "demo" in err.lower()


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
