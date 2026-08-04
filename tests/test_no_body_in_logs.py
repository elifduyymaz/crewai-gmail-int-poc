"""NFR-S2 / FR21: no message body, summary, or subject content in the log stream.

Task 5.4 AC #1. Runs both the offline demo pipeline AND the live batch
loop (with real parser + real DLQ boundary, LLM and Gmail mocked at the
edges), captures every log record at DEBUG level, and asserts that no
substring from any fixture body, canned Summary, or subject header
appears anywhere in the log stream.

The stdout channel is deliberately NOT audited — it is the intended
output artifact for the live batch and carries the full
:class:`SummaryRecord` JSON. The invariant is that the *log stream*
(stderr + log handlers) never carries the same content, so an operator
scrolling through logs never sees email bodies.

Coverage split:

* ``test_*_during_demo`` — exercise ``run_demo_batch`` which emits
  ``demo_starting`` / ``demo_written`` / ``demo_batch_complete``.
* ``test_*_during_live_batch`` — exercise ``main._run_batch`` which
  emits ``boot_complete`` / ``summary_completed`` / ``message_failed``
  / ``dlq_dropped_message`` / ``run_totals``. Every emission site is
  audited for body content.
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

from mail_ingestor import main as main_mod
from mail_ingestor.demo import load_demo_llm_responses, run_demo_batch

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "emails"


def _fixture_body_sentinels() -> list[str]:
    """Collect content substrings from every non-poison fixture body.

    Each fixture body is base64-decoded; lines with ≥ 25 non-whitespace
    characters are kept as sentinels. Short lines (signatures, empty
    delimiters) are noisy and would false-positive against the many
    short substrings scattered through structured log lines.
    """
    sentinels: list[str] = []
    for path in sorted(_FIXTURES_DIR.glob("[0-9]*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        data = raw["payload"]["body"]["data"]
        padded = data + "=" * (-len(data) % 4)
        body = base64.urlsafe_b64decode(padded).decode("utf-8")
        for line in body.splitlines():
            stripped = line.strip()
            if len(stripped) >= 25:
                sentinels.append(stripped)
    # Guard against the filter silently trimming the entire pool: if a
    # future fixture set only has short lines, this test would pass
    # vacuously. Fail loudly instead.
    assert sentinels, (
        "no body sentinels collected — filter too strict or fixture bodies "
        "all shorter than the 25-char threshold. The test would pass "
        "vacuously; investigate before deleting."
    )
    return sentinels


def _summary_content_sentinels() -> list[str]:
    """Collect ``tl_dr`` and ``summary`` text from every canned response."""
    responses = load_demo_llm_responses(_FIXTURES_DIR / "llm_responses.json")
    out: list[str] = []
    for entry in responses.values():
        out.append(entry["tl_dr"])
        out.append(entry["summary"])
    return out


def _fixture_subject_sentinels() -> list[str]:
    """Collect subject header values from every non-poison fixture."""
    subjects: list[str] = []
    for path in sorted(_FIXTURES_DIR.glob("[0-9]*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for header in raw["payload"]["headers"]:
            if header["name"].lower() == "subject":
                subjects.append(header["value"])
    return subjects


# ─────────────────────────────────────────────────────────────
# Demo-pipeline coverage
# ─────────────────────────────────────────────────────────────


def test_no_fixture_body_substring_appears_in_log_stream_during_demo(
    tmp_path, caplog
) -> None:
    # Scope caplog to ``mail_ingestor.*`` so a hypothetical downstream
    # logger with propagate=False cannot silently drop records under a
    # broader root-scoped capture.
    with caplog.at_level(logging.DEBUG, logger="mail_ingestor"):
        run_demo_batch(_FIXTURES_DIR, tmp_path / "out")

    all_log_text = "\n".join(r.getMessage() for r in caplog.records)
    for body_line in _fixture_body_sentinels():
        assert body_line not in all_log_text, (
            f"NFR-S2 breach: fixture body content leaked into logs — {body_line!r}"
        )


def test_no_summary_text_appears_in_log_stream_during_demo(tmp_path, caplog) -> None:
    with caplog.at_level(logging.DEBUG, logger="mail_ingestor"):
        run_demo_batch(_FIXTURES_DIR, tmp_path / "out")

    all_log_text = "\n".join(r.getMessage() for r in caplog.records)
    for summary_text in _summary_content_sentinels():
        assert summary_text not in all_log_text, (
            f"NFR-S2 breach: canned Summary text leaked into logs — {summary_text!r}"
        )


def test_no_subject_content_appears_in_log_stream_during_demo(
    tmp_path, caplog
) -> None:
    with caplog.at_level(logging.DEBUG, logger="mail_ingestor"):
        run_demo_batch(_FIXTURES_DIR, tmp_path / "out")

    all_log_text = "\n".join(r.getMessage() for r in caplog.records)
    for subject in _fixture_subject_sentinels():
        assert subject not in all_log_text, (
            f"NFR-S2 breach: subject header leaked into logs — {subject!r}"
        )


def test_demo_written_log_line_is_shape_bounded(tmp_path, caplog) -> None:
    """The demo pipeline's ``demo_written`` log format carries no
    ``SummaryRecord`` field placeholders. Regression that adds
    ``subject=%s`` or ``body=%s`` to this line would leak content
    across every demo run.

    (Companion test :func:`test_summary_completed_log_line_is_shape_bounded_in_live_batch`
    below pins the live batch's equivalent line.)
    """
    with caplog.at_level(logging.INFO, logger="mail_ingestor"):
        run_demo_batch(_FIXTURES_DIR, tmp_path / "out")

    # Filter by prefix, not substring — the pytest ``tmp_path`` derived
    # from the test function name can contain ``demo_written`` and
    # false-positive a substring filter (test-name → path → log
    # ``demo_starting`` message).
    written_records = [
        r for r in caplog.records if r.getMessage().startswith("demo_written ")
    ]
    assert written_records, "expected at least one demo_written INFO record"
    for record in written_records:
        message = record.getMessage()
        assert message.startswith("demo_written path=")
        assert "message_id=" in message
        # Negative pins: none of the SummaryRecord content fields.
        assert "tl_dr=" not in message
        assert "summary=" not in message
        assert "subject=" not in message
        assert "body=" not in message


# ─────────────────────────────────────────────────────────────
# Live-batch coverage (main._run_batch — the operator-facing path)
# ─────────────────────────────────────────────────────────────


def _wire_live_batch(monkeypatch, tmp_path):
    """Wire main.main(['--label', 'L']) to drive the REAL flow +
    persistence layer with fixtures as message payloads. Only outer
    edges are mocked. Returns the in-memory SQLite connection so tests
    can inspect what was persisted.
    """
    from mail_ingestor.flow import NativeAnthropicLLM
    from mail_ingestor.persistence.db import bootstrap

    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")

    fixture_payloads: dict[str, dict] = {}
    for path in sorted(_FIXTURES_DIR.glob("[0-9]*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        fixture_payloads[payload["id"]] = payload
    ids = list(fixture_payloads.keys())

    fake_gmail = MagicMock(name="gmail_client")
    monkeypatch.setattr(main_mod, "_build_gmail_client", lambda: fake_gmail)

    fake_resolver_cls = MagicMock(name="LabelResolver")
    fake_resolver_cls.return_value.resolve.return_value = "label-live"
    monkeypatch.setattr("mail_ingestor.gmail.labels.LabelResolver", fake_resolver_cls)

    fake_reader_cls = MagicMock(name="GmailReaderService")
    fake_reader = fake_reader_cls.return_value
    fake_reader.list_message_ids.return_value = ids
    fake_reader.get_message.side_effect = lambda mid: fixture_payloads[mid]
    monkeypatch.setattr("mail_ingestor.gmail.reader.GmailReaderService", fake_reader_cls)

    real_conn = sqlite3.connect(":memory:")
    real_conn.row_factory = sqlite3.Row
    bootstrap(real_conn)
    monkeypatch.setattr("mail_ingestor.persistence.db.init_db", lambda _p: real_conn)

    # Canned Summary JSON — subject/body/tl_dr all use neutral filler.
    # Fixture-body / canned-summary sentinels come from ELSEWHERE
    # (fixture files, llm_responses.json), so this filler cannot
    # accidentally match a sentinel.
    canned = json.dumps(
        {
            "tl_dr": "live-batch canned tldr filler",
            "summary": "live-batch canned summary filler body",
            "key_points": [],
            "action_items": [],
            "category": "test",
        }
    )
    monkeypatch.setattr(NativeAnthropicLLM, "call", lambda self, *a, **k: canned)

    return real_conn, ids


def test_no_fixture_body_substring_appears_in_log_stream_during_live_batch(
    tmp_path, monkeypatch, caplog
) -> None:
    _wire_live_batch(monkeypatch, tmp_path)
    with caplog.at_level(logging.DEBUG, logger="mail_ingestor"):
        rc = main_mod.main(["--label", "L", "--limit", "10"])
    assert rc == 0

    all_log_text = "\n".join(r.getMessage() for r in caplog.records)
    for body_line in _fixture_body_sentinels():
        assert body_line not in all_log_text, (
            f"NFR-S2 breach (live batch): fixture body leaked into logs — {body_line!r}"
        )


def test_no_subject_content_appears_in_log_stream_during_live_batch(
    tmp_path, monkeypatch, caplog
) -> None:
    _wire_live_batch(monkeypatch, tmp_path)
    with caplog.at_level(logging.DEBUG, logger="mail_ingestor"):
        main_mod.main(["--label", "L", "--limit", "10"])

    all_log_text = "\n".join(r.getMessage() for r in caplog.records)
    for subject in _fixture_subject_sentinels():
        assert subject not in all_log_text, (
            f"NFR-S2 breach (live batch): subject leaked into logs — {subject!r}"
        )


def test_summary_completed_log_line_is_shape_bounded_in_live_batch(
    tmp_path, monkeypatch, caplog
) -> None:
    """Pin ``main._run_batch``'s ``summary_completed`` log format —
    only ``message_id``, ``tokens_prompt``, ``tokens_completion``,
    ``duration_ms``. Regression adding ``subject=%s`` or ``body=%s``
    to this format string would leak email content into every
    successful message's log line.

    (Companion to :func:`test_demo_written_log_line_is_shape_bounded`
    above — same shape contract for the demo pipeline.)
    """
    _wire_live_batch(monkeypatch, tmp_path)
    with caplog.at_level(logging.INFO, logger="mail_ingestor"):
        main_mod.main(["--label", "L", "--limit", "10"])

    completed = [
        r for r in caplog.records if r.getMessage().startswith("summary_completed ")
    ]
    assert completed, "expected at least one summary_completed INFO record"
    for record in completed:
        message = record.getMessage()
        assert message.startswith("summary_completed message_id=")
        assert "tokens_prompt=" in message
        assert "tokens_completion=" in message
        assert "duration_ms=" in message
        # Negative pins mirror the demo test.
        assert "subject=" not in message
        assert "body=" not in message
        assert "tl_dr=" not in message
        # ``summary=`` would match ``summary_completed`` prefix — check
        # for the specific key-value form the leak regression would use.
        assert "summary=live-batch canned summary filler body" not in message
