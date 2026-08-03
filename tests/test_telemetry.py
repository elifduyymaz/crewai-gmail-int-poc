import logging
from datetime import UTC, datetime

from mail_ingestor.schemas import Summary, SummaryRecord
from mail_ingestor.telemetry import log_run_totals

AWARE = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _record(
    source_message_id: str = "m1",
    *,
    tokens_prompt: int | None = None,
    tokens_completion: int | None = None,
) -> SummaryRecord:
    return SummaryRecord(
        source_message_id=source_message_id,
        subject="Subj",
        summary=Summary(
            tl_dr="tldr",
            summary="the summary body",
            key_points=["k"],
            action_items=[],
            category="report",
        ),
        model="claude-x",
        tokens_prompt=tokens_prompt,
        tokens_completion=tokens_completion,
        created_at=AWARE,
    )


def _run_totals_record(caplog):
    matching = [r for r in caplog.records if "run_totals" in r.getMessage()]
    assert matching, "expected exactly one run_totals INFO record"
    return matching[0]


def test_log_run_totals_empty_records_reports_zero_and_none_sums(caplog):
    with caplog.at_level(logging.INFO, logger="mail_ingestor.telemetry"):
        log_run_totals([])
    message = _run_totals_record(caplog).getMessage()
    assert "messages_processed=0" in message
    assert "tokens_prompt_sum=None" in message
    assert "tokens_completion_sum=None" in message


def test_log_run_totals_sums_populated_records(caplog):
    records = [
        _record("m1", tokens_prompt=100, tokens_completion=30),
        _record("m2", tokens_prompt=200, tokens_completion=70),
        _record("m3", tokens_prompt=50, tokens_completion=10),
    ]
    with caplog.at_level(logging.INFO, logger="mail_ingestor.telemetry"):
        log_run_totals(records)
    message = _run_totals_record(caplog).getMessage()
    assert "messages_processed=3" in message
    assert "tokens_prompt_sum=350" in message
    assert "tokens_completion_sum=110" in message


def test_log_run_totals_ignores_none_records_but_still_counts_them(caplog):
    # A record with None token fields still counts toward messages_processed
    # (the message WAS processed successfully; we just don't have usage
    # data for it). Sums accumulate only the known contributions.
    records = [
        _record("m1", tokens_prompt=100, tokens_completion=30),
        _record("m2"),  # tokens are None
        _record("m3", tokens_prompt=50, tokens_completion=10),
    ]
    with caplog.at_level(logging.INFO, logger="mail_ingestor.telemetry"):
        log_run_totals(records)
    message = _run_totals_record(caplog).getMessage()
    assert "messages_processed=3" in message
    assert "tokens_prompt_sum=150" in message
    assert "tokens_completion_sum=40" in message


def test_log_run_totals_all_none_yields_none_sums(caplog):
    # If no record has usage data, sums propagate as literal None — a signal
    # that no observation happened, not zero.
    records = [_record("m1"), _record("m2")]
    with caplog.at_level(logging.INFO, logger="mail_ingestor.telemetry"):
        log_run_totals(records)
    message = _run_totals_record(caplog).getMessage()
    assert "messages_processed=2" in message
    assert "tokens_prompt_sum=None" in message
    assert "tokens_completion_sum=None" in message


def test_log_run_totals_partial_metrics_accumulate_independently(caplog):
    # A record with prompt but not completion contributes only to prompt_sum.
    records = [
        _record("m1", tokens_prompt=100, tokens_completion=None),
        _record("m2", tokens_prompt=None, tokens_completion=20),
    ]
    with caplog.at_level(logging.INFO, logger="mail_ingestor.telemetry"):
        log_run_totals(records)
    message = _run_totals_record(caplog).getMessage()
    assert "tokens_prompt_sum=100" in message
    assert "tokens_completion_sum=20" in message


def test_log_run_totals_never_leaks_body_content(caplog):
    # NFR-S3 guard: the summary body / subject / category must not enter
    # the aggregated log line, ever.
    records = [
        _record("m1", tokens_prompt=100, tokens_completion=30),
    ]
    with caplog.at_level(logging.INFO, logger="mail_ingestor.telemetry"):
        log_run_totals(records)
    message = _run_totals_record(caplog).getMessage()
    assert "the summary body" not in message
    assert "tldr" not in message
    assert "report" not in message
    assert "Subj" not in message


def test_log_run_totals_uses_info_level(caplog):
    # AC #5 says main.py logs a summary after the batch. INFO is the right
    # level: visible without DEBUG verbosity, quiet by default without
    # further configuration.
    records = [_record("m1", tokens_prompt=10, tokens_completion=5)]
    with caplog.at_level(logging.DEBUG, logger="mail_ingestor.telemetry"):
        log_run_totals(records)
    assert _run_totals_record(caplog).levelno == logging.INFO
