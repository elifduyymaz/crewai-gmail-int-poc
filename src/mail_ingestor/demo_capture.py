"""Post-run helper: turn a live batch's ``run.log`` + ``run.err.log`` into
committed ``demo/00N_sample.json`` samples with PII scrubbed (Task 5.5).

Workflow:

.. code-block:: bash

    # 1. Run the live batch, capture stdout (JSON records) and stderr (logs)
    python -m mail_ingestor.main --label poc/reports --limit 5 \
        > run.log 2> run.err.log

    # 2. Extract, PII-scrub, and commit as demo samples
    python -m mail_ingestor.demo_capture \
        --run-log run.log --err-log run.err.log --output-dir demo

Behavior:

* ``run.log`` (stdout) is parsed line by line. Every line that starts with
  ``{`` and validates as :class:`SummaryRecord` becomes a candidate.
* Each record is fed through :func:`mail_ingestor.redaction.redact_summary_record`
  to strip email addresses from subject and every ``Summary.*`` field.
* Scrubbed records are written to ``output_dir/001_sample.json`` ..
  ``output_dir/N_sample.json`` (compact-indented, one file per record).
* ``run.err.log`` (stderr) is parsed for ``summary_completed ...
  duration_ms=X`` lines; the tool reports per-message latency and batch
  total against Task 5.5's NFR-P1 (< 20s per email) and NFR-P2 (< 2min
  total) thresholds. A non-zero exit code signals an SLO breach so CI
  or an operator can flag it.

Design choices:

* Two-step (batch → capture) instead of a single all-in-one command:
  the AC in ``tasks/5-5-*.md`` explicitly names ``run.log`` as an
  artifact; keeping the shell redirect explicit matches how an
  operator would run this from the terminal and also lets them
  re-scrub without re-hitting Anthropic (Task 5.5 blocks on rate limits).
* ``redact_summary_record`` (output-side) is a distinct operation
  from ``redact_email_message`` (input-side, EFSP-348). Input-side
  runs during every batch; output-side runs only when generating
  demo artifacts. Two responsibilities, two functions — each replaceable.
* Timing thresholds are constants — a future refactor can lift them
  into ``Settings`` if operators want overrides.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from mail_ingestor.redaction import redact_summary_record
from mail_ingestor.schemas import SummaryRecord

logger = logging.getLogger(__name__)

# NFR-P1: per-email latency target. NFR-P2: batch total target.
_PER_EMAIL_LATENCY_TARGET_MS = 20_000
_BATCH_TOTAL_TARGET_MS = 120_000

# ``summary_completed message_id=X tokens_prompt=Y tokens_completion=Z duration_ms=D``
# emitted by ``main._emit_success`` — no other log line carries duration_ms
# in a live batch, so the regex is unambiguous.
_DURATION_LINE_RE = re.compile(
    r"summary_completed\s+message_id=(?P<mid>\S+)\s+.*duration_ms=(?P<ms>\d+)"
)


@dataclass(frozen=True, slots=True)
class BatchTimings:
    """Per-message and total wall-clock timings extracted from ``run.err.log``."""

    per_message_ms: list[tuple[str, int]]  # (message_id, duration_ms)

    @property
    def total_ms(self) -> int:
        return sum(ms for _mid, ms in self.per_message_ms)

    @property
    def slowest_ms(self) -> int:
        return max((ms for _mid, ms in self.per_message_ms), default=0)


def iter_records_from_stdout(stream: TextIO) -> list[SummaryRecord]:
    """Parse a live-batch stdout stream into validated ``SummaryRecord``s.

    Non-JSON lines are silently skipped (main.py's --demo path can
    also emit CrewAI rich-console banners on stdout under some
    invocations; we want to be tolerant). Invalid JSON that starts
    with ``{`` raises loudly — a corrupt run.log is a bug worth
    surfacing, not swallowing.
    """
    records: list[SummaryRecord] = []
    for raw_line in stream:
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        records.append(SummaryRecord.model_validate_json(line))
    return records


def parse_timings_from_stderr(stream: TextIO) -> BatchTimings:
    """Extract per-message ``duration_ms`` from ``summary_completed`` log lines."""
    per_message: list[tuple[str, int]] = []
    for line in stream:
        match = _DURATION_LINE_RE.search(line)
        if match is None:
            continue
        per_message.append((match.group("mid"), int(match.group("ms"))))
    return BatchTimings(per_message_ms=per_message)


def write_scrubbed_samples(
    records: list[SummaryRecord], output_dir: Path
) -> list[Path]:
    """Apply output-side PII scrub and write ``output_dir/00N_sample.json``.

    Any pre-existing ``*_sample.json`` in ``output_dir`` is removed first —
    a shrinking record set must not leave orphaned files (mirrors the
    Task 5.3 ``run_demo_batch`` invariant).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in output_dir.glob("*_sample.json"):
        stale.unlink()

    written: list[Path] = []
    for index, record in enumerate(records, start=1):
        scrubbed = redact_summary_record(record)
        path = output_dir / f"{index:03d}_sample.json"
        path.write_text(scrubbed.model_dump_json(indent=2) + "\n", encoding="utf-8")
        written.append(path)
        logger.info(
            "demo_capture_written path=%s source_message_id=%s",
            path,
            scrubbed.source_message_id,
        )
    return written


def check_timings_against_slo(timings: BatchTimings) -> list[str]:
    """Return a list of human-readable SLO breach descriptions (empty if OK).

    Threshold sources:
    * per-email: NFR-P1 (20s/message)
    * batch total: NFR-P2 (2min)
    Both from Task 5.5 AC #7.
    """
    breaches: list[str] = []
    for mid, ms in timings.per_message_ms:
        if ms > _PER_EMAIL_LATENCY_TARGET_MS:
            breaches.append(
                f"NFR-P1 breach: message_id={mid} took {ms} ms "
                f"(> {_PER_EMAIL_LATENCY_TARGET_MS} ms per-email budget)"
            )
    if timings.total_ms > _BATCH_TOTAL_TARGET_MS:
        breaches.append(
            f"NFR-P2 breach: batch total {timings.total_ms} ms "
            f"(> {_BATCH_TOTAL_TARGET_MS} ms batch budget)"
        )
    return breaches


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mail-ingestor-demo-capture",
        description="Extract SummaryRecord JSONs from a live run.log, apply PII scrub, "
        "commit as demo/00N_sample.json with a timing report.",
    )
    parser.add_argument(
        "--run-log",
        type=Path,
        required=True,
        help="Path to the live-batch stdout capture (each line is one SummaryRecord JSON).",
    )
    parser.add_argument(
        "--err-log",
        type=Path,
        default=None,
        help="Optional path to the stderr capture; used for timing analysis.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("demo"),
        help="Where to write the scrubbed demo samples (default: demo/).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = build_parser().parse_args(argv)

    with args.run_log.open(encoding="utf-8") as stream:
        records = iter_records_from_stdout(stream)
    if not records:
        print(
            f"[demo-capture] no SummaryRecord JSON found in {args.run_log}",
            file=sys.stderr,
        )
        return 1

    written = write_scrubbed_samples(records, args.output_dir)
    logger.info("demo_capture_complete count=%d output_dir=%s", len(written), args.output_dir)

    if args.err_log is not None and args.err_log.exists():
        with args.err_log.open(encoding="utf-8") as err_stream:
            timings = parse_timings_from_stderr(err_stream)
        if timings.per_message_ms:
            print(
                f"[demo-capture] timings: {len(timings.per_message_ms)} messages, "
                f"total={timings.total_ms} ms, slowest={timings.slowest_ms} ms",
                file=sys.stderr,
            )
            breaches = check_timings_against_slo(timings)
            for b in breaches:
                print(f"[demo-capture] {b}", file=sys.stderr)
            if breaches:
                return 2
        else:
            print(
                f"[demo-capture] no timing lines found in {args.err_log}; "
                "SLO check skipped",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
