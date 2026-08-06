# Live-Run Demo Generation Workflow (Task 5.5)

Steps to produce the committed `demo/live-run/00N_sample.json` deliverable
for Ali Murat's Journey 1 review, from a live Gmail account through PII
scrubbing to committed samples.

> **Two-layer `demo/` layout.** `demo/*.json` holds the *offline* canned
> samples that `--demo` regenerates deterministically (guarded by the
> `test_committed_demo_samples_match_a_fresh_run` invariant). Live-run
> evidence lives under `demo/live-run/*.json` so the two never overwrite
> each other. This workflow writes to the `live-run/` subdirectory.

## Prerequisites (operator-side)

1. **`credentials.json`** at the repo root — a Desktop-app OAuth 2.0
   Client ID from Google Cloud Console. Enable the Gmail API on the
   project. Add the test Gmail user to the OAuth consent screen's
   test-user list.
2. **Test Gmail account** with the label `poc/reports` (case-sensitive)
   applied to at least 5 diverse messages. Prefer types that mirror
   the offline fixture set: weekly report, meeting recap, invoice,
   newsletter, action-item reminder.
3. **`.env`** at the repo root with (at minimum):

   ```env
   ANTHROPIC_AUTH_TOKEN=sk-ant-oat01-...   # obtained via `claude setup-token`
   LLM_MODEL=claude-haiku-4-5-20251001     # Haiku for rate-limit headroom
   GMAIL_CREDENTIALS_PATH=credentials.json
   GMAIL_TOKEN_PATH=token.json
   SQLITE_DB_PATH=mail_ingestor.db
   LOG_LEVEL=INFO
   ```

## Workflow

```bash
# 1. First-time only: browser OAuth to write token.json
uv run python -m mail_ingestor.main auth

# 2. Live batch. stdout = one SummaryRecord JSON per line; stderr = log stream.
uv run python -m mail_ingestor.main \
    --label poc/reports --limit 5 \
    > run.log 2> run.err.log

# 3. Extract → PII-scrub → commit as demo/live-run/00N_sample.json + SLO report.
uv run python -m mail_ingestor.demo_capture \
    --run-log run.log \
    --err-log run.err.log \
    --output-dir demo/live-run

# 4. Hand-inspect each demo/live-run/*.json for stray PII that the regex
#    didn't catch (rare names, unusual identifiers). Edit in place if needed.
#    Re-running step 3 without re-running step 2 does NOT re-hit Anthropic.

# 5. Commit the samples.
git add demo/live-run/
git commit -m "chore(demo): live-run samples with PII scrub"
```

## Exit codes from `demo_capture`

| Code | Meaning |
|---|---|
| 0 | Samples written, all timings within SLO |
| 1 | `run.log` contained no `SummaryRecord` JSON (batch failed?) |
| 2 | Samples written but one or more SLO breach (NFR-P1 > 20s/email or NFR-P2 > 2min total) |

## SLO thresholds

* **NFR-P1**: per-email latency ≤ 20,000 ms (from `duration_ms=X` in `summary_completed` log)
* **NFR-P2**: batch total ≤ 120,000 ms

Breaches don't rewrite the samples — they signal "this run is not
representative of the production SLO." Investigate the log for LLM
retries, Gmail transient errors, etc.

## Rate-limit contingency

The Claude subscription seed token has a rolling-window quota. If the
batch fails partway with a 429, wait for the window to reset or refresh
the token (`claude setup-token`). The DLQ boundary (Task 5.2) means the
run still exits 0; DLQ'd rows will show up in `run_totals`.

## "How the demo was produced" — findings.md paragraph draft

Copy the following into `findings.md` § deliverable notes when Story 5.6
lands. Fill in the timing numbers and any hand-edit notes from your run:

> The five committed `demo/live-run/00N_sample.json` samples were produced by a
> live run against a test Gmail account, label `poc/reports`, on
> `<DATE>`. Command: `python -m mail_ingestor.main --label poc/reports
> --limit 5 > run.log 2> run.err.log`. Wall-clock timings from
> `demo_capture --err-log`: per-email `<MIN>–<MAX>` ms (SLO ≤ 20 s
> NFR-P1), batch total `<TOTAL>` ms (SLO ≤ 120 s NFR-P2). PII was
> scrubbed by `mail_ingestor.demo_capture` — every email address in
> subject, `Summary.tl_dr`, `Summary.summary`, `Summary.key_points`,
> and `Summary.action_items` was replaced with `[scrubbed]@example.com`.
> Hand-inspection removed `<COUNT>` further sensitive tokens
> (`<what>`). Model: `claude-haiku-4-5-20251001`. Tokens consumed:
> `<PROMPT_SUM>` prompt + `<COMPLETION_SUM>` completion.
