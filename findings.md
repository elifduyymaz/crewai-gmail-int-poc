## Verdict: 🟡 YELLOW — proceed with CrewAI for the Ingestion Pipeline, contingent on four production must-adds (CVE patching + container sandbox, external idempotency claim, LLM-proxy cost cap, continued ownership of the custom GmailTool). No operational blocker fired at PoC scale; every gap is addressable at the platform boundary.

## What we did
Built an end-to-end mail-ingestion PoC on CrewAI Flow: Gmail read via custom `BaseTool` wrappers (list-by-label + get-message), MIME parsed, PII scrubbed on both the input and the output side, summarised through Anthropic Claude (`claude-haiku-4-5-20251001`), and persisted to SQLite with `INSERT OR IGNORE` idempotency. The full pipeline was exercised against a controlled `poc/reports` Gmail label; five samples ship redacted under `demo/`.

## What worked / What surprised us / What we'd change
| What worked | What surprised us | What we'd change |
|---|---|---|
| Flow orchestration ran end-to-end unattended on 5 messages, 0 DLQ, ~20 s wall-clock. | The summariser reproduces personal names verbatim from message bodies — output-side regex scrubbing does not catch them (needed to hand-redact two spans before commit). | Move the idempotency claim outside the tool boundary (external Redis / Postgres) instead of relying only on DB-layer `INSERT OR IGNORE`. |
| Two-layer PII scrub (input + output) held across the 5-message sample; zero `@`-shaped addresses reached `demo/`. DLQ isolation is unit-test-covered but was not exercised live (0 DLQ this run). | CrewAI's Rich console pollutes stdout; a `line.startswith("{")` filter in `demo_capture` was needed to recover clean JSON. | Add a named-entity redaction pass at the output layer so hand-redaction is no longer required. |
| Idempotency envelope (`INSERT OR IGNORE` on `message_id`) is in place and did not trigger — the batch produced 5 unique writes, as expected. | Prompt size averaged ~790 tokens per message on short-form email — MIME/HTML markup carries more weight than expected, so production summarisation should strip HTML upstream to cut cost. | Wire the LLM proxy (`litellm-proxy` with per-project daily spend cap) from day one, not as a later hardening step. |

## 4-gap observations
| Gap | Observed at PoC scale? | Evidence |
|---|---|---|
| 4 open CVEs (CERT/CC 2026-03-30 — RCE, arbitrary file read, SSRF, prompt-injection chaining) | No | PoC ran on an isolated dev machine per PRD § PoC approach; no CVE audit, no sandbox, and prompt-injection resistance was not exercised against adversarial email bodies. Production build must pin a patched CrewAI release, add `pip-audit` in CI, containerise, and add an injection-defence layer before untrusted body content reaches the summariser. |
| Issue [#5802](https://github.com/crewAIInc/crewAI/issues/5802) — retry-side idempotency (tool functions re-execute on retry with no guard) | Not triggered | 5-message batch completed with 0 DLQ and 0 retries, so the failure mode never fired. Structural envelope is `INSERT OR IGNORE` on `message_id`; a back-to-back re-run would exercise it empirically. External claim (Redis/Postgres) remains the production answer. |
| No native cost cap (no `max_tokens_per_run`, no dollar cap, no circuit breaker in the framework) | No | Batch stayed well inside the budget: 3953 prompt + 1245 completion tokens over ~20 s, slowest call 4181 ms. Agent-boundary caps (`max_iter=10`, `max_execution_time=120s`) held. Production must add an external LLM proxy with per-project daily spend limit — not optional. |
| No first-party `GmailTool` in `crewai-tools` v1.15.1 | Yes — gap closed | Custom `BaseTool` in `src/mail_ingestor/tools/gmail_tool.py` (59 lines) wraps `google-api-python-client` with zero third-party SaaS in the data path. Continuing ownership is expected; upstream is unlikely to ship one on our timeline. |

## Recommendation to Ingestion Pipeline design
**Go**, with four production must-adds (in priority order):
1. Pin a patched CrewAI release, add `pip-audit` / `safety` to CI, run the runtime in a sandboxed container. No banking-context deployment before this.
2. External idempotency claim (Redis or Postgres) around every side-effecting tool call, keyed on `message_id`. `INSERT OR IGNORE` at the DB layer is a safety net, not the primary defence.
3. LLM proxy (`litellm-proxy` or equivalent) with per-project daily spend limit, wired as the OpenAI-compat endpoint. Framework provides no native ceiling; this is not optional.
4. Retain and version the custom `GmailTool` wrapper (~60 lines); do not wait for an upstream first-party equivalent.

**Alternative frameworks:** revisit only if (a) the CVE cadence proves unsustainable, or (b) the external-claim envelope grows into a wearing operational cost. Both are addressable inside CrewAI today, so no immediate switch is warranted.

## How the demo was produced
Five messages in a single controlled Gmail account, all tagged with the `poc/reports` label — high-volume inboxes, mixed labels, and multi-tenant scale are explicitly out of scope for this PoC. `uv run python -m mail_ingestor.main --label poc/reports --limit 5` ran the flow end-to-end, emitting structured JSON on stdout and telemetry on stderr. `uv run python -m mail_ingestor.demo_capture` aggregated the run, applied the output-side PII scrub, and wrote `demo/001–005_sample.json`. Two spans that the summariser reproduced from message bodies — one sender name, one institution name — were hand-redacted with `[REDACTED_NAME]` / `[REDACTED_INSTITUTION]` before commit; automating this is a follow-up on the output-side scrubber. Full reproduction steps are in `docs/live_demo_workflow.md`.

## References
- [`demo/`](demo/) — five redacted live-run samples
- [`docs/live_demo_workflow.md`](docs/live_demo_workflow.md) — reproduction steps for the demo
- [`../base-docs/technical-crewai-gmail-integration-poc-research-2026-07-07.md`](../base-docs/technical-crewai-gmail-integration-poc-research-2026-07-07.md) — full research, incl. Chapter 8 recommendations
- [`../base-docs/architecture.md`](../base-docs/architecture.md) — § Decision-support artifact discipline (line 83)
- [`../base-docs/prd.md`](../base-docs/prd.md) — FR29 (findings format) + FR33 (decision-enablement)

## Sign-off
- AI Dev: __________________
- FSD: __________________
