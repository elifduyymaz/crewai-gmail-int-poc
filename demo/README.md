# `demo/` — two-layer sample set

Two different kinds of samples live here. They are kept in separate
directories so `--demo` regeneration and live-run evidence never
overwrite each other.

| Path | Origin | Deterministic? | Purpose |
|---|---|---|---|
| `demo/*.json` | `python -m mail_ingestor.main --demo` | ✅ byte-identical across runs | Offline reproducibility check — `test_committed_demo_samples_match_a_fresh_run` in `tests/test_demo.py` asserts these match a fresh `--demo` run. Regenerate with `--demo` and commit. |
| `demo/live-run/*.json` | `mail_ingestor.demo_capture` against a real Gmail label | ❌ live LLM output, PII-scrubbed | Live-run evidence referenced by [`findings.md`](../findings.md). Reproduction steps in [`../docs/live_demo_workflow.md`](../docs/live_demo_workflow.md). Regeneration writes to `--output-dir demo/live-run`. |

If you ran `--demo` and see `demo/*.json` changed, that is expected
maintenance — commit the update. `demo/live-run/` is not touched by
`--demo`.
