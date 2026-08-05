"""Tests for the post-run demo capture CLI (Task 5.5)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest

from mail_ingestor.demo_capture import (
    BatchTimings,
    build_parser,
    check_timings_against_slo,
    iter_records_from_stdout,
    parse_timings_from_stderr,
    write_scrubbed_samples,
)
from mail_ingestor.demo_capture import (
    main as demo_capture_main,
)
from mail_ingestor.schemas import Summary, SummaryRecord


def _record(
    source_message_id: str = "live-1",
    *,
    subject: str = "Weekly update from alice@example.com",
    summary_text: str = "Alice at alice@example.com reported growth.",
) -> SummaryRecord:
    return SummaryRecord(
        source_message_id=source_message_id,
        subject=subject,
        summary=Summary(
            tl_dr="growth up 20%",
            summary=summary_text,
            key_points=["+20% sales"],
            action_items=[],
            category="report",
        ),
        model="claude-haiku-4-5-20251001",
        tokens_prompt=500,
        tokens_completion=120,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


# ─────────────────────────────────────────────────────────────
# iter_records_from_stdout
# ─────────────────────────────────────────────────────────────


def test_iter_records_from_stdout_parses_valid_json_lines() -> None:
    stream = StringIO(
        _record("m1").model_dump_json()
        + "\n"
        + _record("m2").model_dump_json()
        + "\n"
    )
    records = iter_records_from_stdout(stream)
    assert [r.source_message_id for r in records] == ["m1", "m2"]


def test_iter_records_from_stdout_skips_non_json_lines() -> None:
    # CrewAI's rich console emits banner text on stdout that starts with
    # box-drawing characters or blank lines. Ignore anything that doesn't
    # start with ``{``.
    stream = StringIO(
        "╭─── Flow Execution ───╮\n"
        + _record("m1").model_dump_json()
        + "\n"
        + "\n"
        + "  some banner text  \n"
        + _record("m2").model_dump_json()
        + "\n"
    )
    records = iter_records_from_stdout(stream)
    assert len(records) == 2


def test_iter_records_from_stdout_raises_on_malformed_json_starting_with_brace() -> None:
    # A line that looks like a record but is malformed is a real bug —
    # don't silently drop it.
    stream = StringIO('{"source_message_id": "broken", "subject":\n')
    with pytest.raises(Exception):  # noqa: B017 - Pydantic or JSON error, both acceptable
        iter_records_from_stdout(stream)


# ─────────────────────────────────────────────────────────────
# parse_timings_from_stderr
# ─────────────────────────────────────────────────────────────


def test_parse_timings_extracts_message_id_and_duration_ms() -> None:
    stderr = StringIO(
        "2026-08-05 10:00:00 INFO mail_ingestor.main "
        "summary_completed message_id=live-1 tokens_prompt=500 "
        "tokens_completion=120 duration_ms=12345\n"
        "2026-08-05 10:00:15 INFO mail_ingestor.main "
        "summary_completed message_id=live-2 tokens_prompt=440 "
        "tokens_completion=90 duration_ms=8000\n"
    )
    timings = parse_timings_from_stderr(stderr)
    assert timings.per_message_ms == [("live-1", 12345), ("live-2", 8000)]
    assert timings.total_ms == 20345
    assert timings.slowest_ms == 12345


def test_parse_timings_ignores_unrelated_log_lines() -> None:
    stderr = StringIO(
        "2026-08-05 10:00:00 INFO mail_ingestor.main boot_complete model=x\n"
        "2026-08-05 10:00:01 INFO mail_ingestor.telemetry run_totals ...\n"
        "2026-08-05 10:00:02 ERROR mail_ingestor.main message_failed ...\n"
    )
    assert parse_timings_from_stderr(stderr).per_message_ms == []


def test_parse_timings_batch_totals_empty_for_zero_records() -> None:
    empty = BatchTimings(per_message_ms=[])
    assert empty.total_ms == 0
    assert empty.slowest_ms == 0


# ─────────────────────────────────────────────────────────────
# write_scrubbed_samples
# ─────────────────────────────────────────────────────────────


def test_write_scrubbed_samples_writes_one_file_per_record(tmp_path: Path) -> None:
    out_dir = tmp_path / "demo"
    written = write_scrubbed_samples(
        [_record("m1"), _record("m2"), _record("m3")], out_dir
    )
    assert [p.name for p in written] == [
        "001_sample.json",
        "002_sample.json",
        "003_sample.json",
    ]
    for p in written:
        assert p.read_text(encoding="utf-8").endswith("\n")


def test_write_scrubbed_samples_output_carries_no_raw_email_addresses(
    tmp_path: Path,
) -> None:
    # AC #5: sender field (and every text field) scrubbed. The subject
    # of _record() embeds ``alice@example.com``; the scrubbed sample
    # must not carry it.
    out_dir = tmp_path / "demo"
    written = write_scrubbed_samples([_record("m1")], out_dir)
    body = written[0].read_text(encoding="utf-8")
    assert "alice@example.com" not in body
    assert "[scrubbed]@example.com" in body
    # Provenance intact — source_message_id and model are not PII.
    payload = json.loads(body)
    assert payload["source_message_id"] == "m1"
    assert payload["model"] == "claude-haiku-4-5-20251001"


def test_write_scrubbed_samples_prunes_stale_files(tmp_path: Path) -> None:
    out_dir = tmp_path / "demo"
    out_dir.mkdir()
    stale = out_dir / "006_sample.json"
    stale.write_text('{"stale": true}', encoding="utf-8")
    assert stale.exists()

    write_scrubbed_samples([_record("m1")], out_dir)

    assert not stale.exists()


# ─────────────────────────────────────────────────────────────
# check_timings_against_slo
# ─────────────────────────────────────────────────────────────


def test_check_timings_no_breach_when_under_targets() -> None:
    timings = BatchTimings(per_message_ms=[("m1", 5000), ("m2", 3000)])
    assert check_timings_against_slo(timings) == []


def test_check_timings_reports_per_email_breach() -> None:
    timings = BatchTimings(per_message_ms=[("m1", 25_000)])
    breaches = check_timings_against_slo(timings)
    assert any("NFR-P1 breach" in b for b in breaches)
    assert any("m1" in b for b in breaches)


def test_check_timings_reports_batch_total_breach() -> None:
    # 6 messages at 25s each = 150s > 120s target.
    timings = BatchTimings(per_message_ms=[(f"m{i}", 25_000) for i in range(6)])
    breaches = check_timings_against_slo(timings)
    assert any("NFR-P1 breach" in b for b in breaches)
    assert any("NFR-P2 breach" in b for b in breaches)


# ─────────────────────────────────────────────────────────────
# CLI end-to-end
# ─────────────────────────────────────────────────────────────


def _write_run_log(path: Path, records: list[SummaryRecord]) -> None:
    path.write_text(
        "\n".join(r.model_dump_json() for r in records) + "\n",
        encoding="utf-8",
    )


def _write_err_log(path: Path, per_message_ms: list[tuple[str, int]]) -> None:
    lines = [
        f"2026-08-05 10:00:00 INFO mail_ingestor.main "
        f"summary_completed message_id={mid} tokens_prompt=500 "
        f"tokens_completion=100 duration_ms={ms}"
        for mid, ms in per_message_ms
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_main_writes_samples_and_returns_0_on_clean_run(tmp_path: Path, capsys) -> None:
    run_log = tmp_path / "run.log"
    err_log = tmp_path / "run.err.log"
    output_dir = tmp_path / "demo"

    records = [_record(f"live-{i}") for i in range(1, 6)]
    _write_run_log(run_log, records)
    _write_err_log(
        err_log,
        [(r.source_message_id, 5000) for r in records],  # under budget
    )

    rc = demo_capture_main(
        [
            "--run-log",
            str(run_log),
            "--err-log",
            str(err_log),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert rc == 0
    written = sorted(output_dir.glob("*_sample.json"))
    assert len(written) == 5
    for w in written:
        assert "alice@example.com" not in w.read_text(encoding="utf-8")


def test_main_returns_1_when_run_log_has_no_records(tmp_path: Path, capsys) -> None:
    run_log = tmp_path / "run.log"
    run_log.write_text("only banner text\nno JSON here\n", encoding="utf-8")

    rc = demo_capture_main(
        ["--run-log", str(run_log), "--output-dir", str(tmp_path / "demo")]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "no SummaryRecord JSON found" in err


def test_main_returns_2_when_slo_breached(tmp_path: Path, capsys) -> None:
    run_log = tmp_path / "run.log"
    err_log = tmp_path / "run.err.log"
    output_dir = tmp_path / "demo"

    records = [_record("slow-1")]
    _write_run_log(run_log, records)
    # 25s > 20s per-email budget.
    _write_err_log(err_log, [("slow-1", 25_000)])

    rc = demo_capture_main(
        [
            "--run-log",
            str(run_log),
            "--err-log",
            str(err_log),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "NFR-P1 breach" in err


def test_main_skips_slo_check_when_err_log_missing(tmp_path: Path) -> None:
    run_log = tmp_path / "run.log"
    _write_run_log(run_log, [_record("m1")])
    rc = demo_capture_main(
        ["--run-log", str(run_log), "--output-dir", str(tmp_path / "demo")]
    )
    assert rc == 0


def test_build_parser_shape() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["--run-log", "run.log", "--err-log", "run.err", "--output-dir", "demo"]
    )
    assert args.run_log == Path("run.log")
    assert args.err_log == Path("run.err")
    assert args.output_dir == Path("demo")


def test_build_parser_run_log_is_required() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_main_emits_info_log_per_written_sample(tmp_path: Path, caplog) -> None:
    run_log = tmp_path / "run.log"
    _write_run_log(run_log, [_record("m1"), _record("m2")])
    with caplog.at_level(logging.INFO, logger="mail_ingestor.demo_capture"):
        demo_capture_main(
            ["--run-log", str(run_log), "--output-dir", str(tmp_path / "demo")]
        )
    written_records = [
        r for r in caplog.records if r.getMessage().startswith("demo_capture_written ")
    ]
    assert len(written_records) == 2
