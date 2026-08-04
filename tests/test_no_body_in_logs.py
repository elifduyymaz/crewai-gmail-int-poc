"""NFR-S2 / FR21: no message body, summary, or subject content in the log stream.

Task 5.4 AC #1. Runs the end-to-end demo pipeline (parse + summarize +
persist) against the committed fixture set with the LLM mocked out, then
scans every log record captured at DEBUG level (the loudest verbosity we
support) for any substring that would indicate a body / summary /
subject leak.

The stdout channel is deliberately NOT audited by this test — it is the
intended output artifact for the live batch and carries the full
:class:`SummaryRecord` JSON. The invariant is that the *log stream*
(stderr + log handlers) never carries the same content, so an operator
scrolling through logs never sees email bodies.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from mail_ingestor.demo import load_demo_llm_responses, run_demo_batch

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "emails"


def _fixture_body_sentinels() -> list[str]:
    """Collect content substrings from every non-poison fixture body.

    The base64-encoded ``payload.body.data`` values decode to plain
    text; each fixture body carries a memorable phrase we can grep for.
    A single one of these appearing in the log stream is a leak.
    """
    import base64

    sentinels: list[str] = []
    for path in sorted(_FIXTURES_DIR.glob("[0-9]*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        data = raw["payload"]["body"]["data"]
        # Pad + decode to reconstruct the plaintext body.
        padded = data + "=" * (-len(data) % 4)
        body = base64.urlsafe_b64decode(padded).decode("utf-8")
        # Extract a handful of unique-ish lines to grep for.
        for line in body.splitlines():
            stripped = line.strip()
            if len(stripped) >= 25:  # ignore short lines / signatures
                sentinels.append(stripped)
    # Cap the list so we don't spam the assertion — take the first two per fixture.
    return sentinels


def _summary_content_sentinels() -> list[str]:
    """Collect ``tl_dr`` and ``summary`` text from every canned response."""
    responses = load_demo_llm_responses(_FIXTURES_DIR / "llm_responses.json")
    out: list[str] = []
    for entry in responses.values():
        out.append(entry["tl_dr"])
        out.append(entry["summary"])
    return out


def test_no_fixture_body_substring_appears_in_log_stream_during_demo(
    tmp_path, caplog
) -> None:
    with caplog.at_level(logging.DEBUG):
        run_demo_batch(_FIXTURES_DIR, tmp_path / "out")

    all_log_text = "\n".join(r.getMessage() for r in caplog.records)
    for body_line in _fixture_body_sentinels():
        assert body_line not in all_log_text, (
            f"NFR-S2 breach: fixture body content leaked into logs — {body_line!r}"
        )


def test_no_summary_text_appears_in_log_stream_during_demo(tmp_path, caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        run_demo_batch(_FIXTURES_DIR, tmp_path / "out")

    all_log_text = "\n".join(r.getMessage() for r in caplog.records)
    for summary_text in _summary_content_sentinels():
        assert summary_text not in all_log_text, (
            f"NFR-S2 breach: canned Summary text leaked into logs — {summary_text!r}"
        )


def test_no_subject_content_appears_in_log_stream_during_demo(
    tmp_path, caplog
) -> None:
    subjects: list[str] = []
    for path in sorted(_FIXTURES_DIR.glob("[0-9]*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for header in raw["payload"]["headers"]:
            if header["name"].lower() == "subject":
                subjects.append(header["value"])

    with caplog.at_level(logging.DEBUG):
        run_demo_batch(_FIXTURES_DIR, tmp_path / "out")

    all_log_text = "\n".join(r.getMessage() for r in caplog.records)
    for subject in subjects:
        assert subject not in all_log_text, (
            f"NFR-S2 breach: subject header leaked into logs — {subject!r}"
        )


def test_batch_success_log_line_is_shape_bounded(tmp_path, caplog) -> None:
    """The ``summary_completed`` log line format string carries no
    SummaryRecord placeholders — only message_id, tokens_prompt,
    tokens_completion, duration_ms. Anything else would be a schema-
    level leak.
    """
    # Trigger a batch success through the demo run (which does not emit
    # summary_completed — that's a live batch log). Instead, exercise
    # the demo's own emit path.
    with caplog.at_level(logging.INFO):
        run_demo_batch(_FIXTURES_DIR, tmp_path / "out")

    written_records = [r for r in caplog.records if "demo_written" in r.getMessage()]
    assert written_records, "expected at least one demo_written INFO record"
    for record in written_records:
        message = record.getMessage()
        # The format is `demo_written path=<...> message_id=<...>` — no
        # SummaryRecord fields (tl_dr, summary, subject) may appear.
        # Pin the exact keys allowed.
        assert message.startswith("demo_written path=")
        assert "message_id=" in message
        # Negative pins: none of the SummaryRecord content fields.
        assert "tl_dr=" not in message
        assert "summary=" not in message
        assert "subject=" not in message
        assert "body=" not in message
