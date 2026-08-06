# crewai-gmail-int-poc

A framework-selection spike: read Gmail-labeled emails, summarize each via a
CrewAI-orchestrated LLM, and emit a structured `SummaryRecord`. The core is
plain Python; CrewAI lives behind a strict two-file fence so the pipeline
ports cleanly to LangGraph or Pydantic AI if the framework decision lands
elsewhere.

The framework verdict is in [findings.md](findings.md): **🟡 YELLOW — proceed
with CrewAI, contingent on four production must-adds.**

## Setup

Three commands take a fresh clone to a live run. Prerequisite (one-time):
install [`uv`](https://docs.astral.sh/uv/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`.

```bash
uv sync                                            # 1. create the venv + install pinned deps
uv run python -m mail_ingestor.main auth           # 2. run the Gmail OAuth installed-app flow
uv run python -m mail_ingestor.main --label <NAME> # 3. summarize the first messages under a label
```

Before step 3, put a Claude **seed token** (not an Anthropic API key) in
`.env` — see [Authentication](#authentication) below. Copy `.env.example`
to `.env` first (`cp .env.example .env`).

> **Note on the commands.** The console script `mail-ingestor` is installed by
> `uv sync`, so `uv run mail-ingestor auth` and
> `uv run mail-ingestor --label <NAME>` are equivalent to steps 2–3 above.
> The task's acceptance criteria named step 2 as `python -m mail_ingestor.auth
> --setup`; the shipped entrypoint is the `auth` subcommand on
> `mail_ingestor.main` shown above (there is no standalone `mail_ingestor.auth`
> module). The commands here are the ones that actually run.

### Zero-credential reproduction (`--demo`)

To verify the pipeline end-to-end on a fresh machine **without** any Gmail or
Claude credentials, run the offline demo — it replays five committed fixtures
through the parse → redact → summarize → persist path with canned LLM
responses and writes redacted JSON to `demo/`:

```bash
uv sync
uv run mail-ingestor --demo
```

On a fresh clone this completes in **well under a minute** (measured ~4 s after
`uv sync`), comfortably inside the ≤ 10-minute fresh-machine reproducibility
target (NFR-M3). No network, no keys.

### Authentication

This project is **keyless**: it uses a Claude Code **seed token** (OAuth),
never an `ANTHROPIC_API_KEY`. In a separate terminal:

```bash
claude setup-token
```

The command opens a browser OAuth flow tied to your Claude subscription and
prints an `sk-ant-oat01-...` value; paste it into `.env` as
`ANTHROPIC_AUTH_TOKEN`. It is handed to the Anthropic SDK's `auth_token`
parameter — see the [rate-limit note](#notes) below. Gmail uses the standard
installed-app OAuth flow (`credentials.json` → `token.json`), both gitignored.

## Import Fence Audit

The core stays framework-agnostic by confining every `crewai` import to a
**two-file fence**: `flow.py` and `tools/gmail_tool.py`. This is enforced
mechanically by the `no-crewai-in-core` pre-commit pygrep hook and by
`tests/test_discipline.py`.

Live audit (line-anchored to actual `import` / `from` statements):

```console
$ grep -REn '^\s*(from|import)\s+crewai' src/mail_ingestor
src/mail_ingestor/flow.py:30:from crewai import Agent, Crew, Process, Task
src/mail_ingestor/flow.py:31:from crewai.flow import Flow, listen, start
src/mail_ingestor/flow.py:32:from crewai.llms.base_llm import BaseLLM
src/mail_ingestor/flow.py:33:from crewai.utilities.types import LLMMessage

src/mail_ingestor/tools/gmail_tool.py:13:from crewai.tools import BaseTool, EnvVar

$ grep -REl '^\s*(from|import)\s+crewai' src/mail_ingestor | wc -l
2
```

**Exactly 2 files** import `crewai`, as required (FR31/FR32).

> **Why not `grep -R "from crewai\|import crewai" src/`?** That un-anchored
> pattern also matches the *string* `import crewai` where it appears inside
> docstrings and comments (in `llm.py`, `main.py`, and `demo.py`, which
> discuss the fence but do not import CrewAI), inflating the count to 8 lines
> across 5 files. Anchoring to `^\s*(from|import)` matches only real
> statements, which is what the fence is about — and yields the true answer:
> 2 files.

## What's Reusable / What Isn't

If the framework decision lands against CrewAI, the core ports to LangGraph or
Pydantic AI with no throwaway code. The split is mechanical, not aspirational —
it is the same boundary the import fence enforces.

| ✅ Reusable (framework-agnostic, plain Python) | Responsibility |
|---|---|
| `schemas.py` | Pydantic boundary contracts (`EmailMessage`, `Summary`, `SummaryRecord`, `DeadLetterRecord`) |
| `gmail/` (`auth`, `labels`, `reader`, `parser`) | Gmail OAuth, label resolution, message read, MIME → `EmailMessage` |
| `persistence/` (`db`, `writer`, `dlq`) | SQLite vault + dead-letter queue |
| `redaction.py` | PII scrub (email masking) — wired input-side in the pipeline |
| `config.py` | `Settings` dataclass + `.env` loader (keyless auth) |
| `llm.py` | Native Anthropic SDK provider — the sole provider point |

| ❌ Not reusable (CrewAI-bound — the fence) | Why |
|---|---|
| `flow.py` | `MailIngestorFlow` on CrewAI `Flow`/`Agent`/`Crew`/`Task` + `BaseLLM` adapter |
| `tools/gmail_tool.py` | CrewAI `BaseTool` wrappers over the Gmail services |

> The AC lists the config module as `settings.py`; the shipped file is
> `config.py` (it defines `class Settings`). Everything else in the reusable
> list matches the AC exactly.

Rebuilding `flow.py` + `gmail_tool.py` against a different orchestrator is the
only work a framework swap requires — estimated ~2 dev-days.

## Explicit Non-Goals

This is a **PoC at PoC scale** (one controlled Gmail account, five
`poc/reports`-labeled messages). The following are explicitly out of scope and
deferred to a hardened production build (see [findings.md](findings.md) for the
evidence behind each). The list is the full PRD §PoC-Approach exclusion set,
plus the four production must-adds surfaced by `findings.md`:

- **Real bank mailbox access** — the PoC targets a controlled test Gmail
  account; integration with a real bank mailbox (and the compliance envelope
  that comes with it) is production-scope.
- **Scale & multi-tenancy** — high-volume inboxes and multi-tenant operation
  are not addressed. Related: **multi-label parallel processing** — the
  pipeline processes a single label per invocation; parallel/fanned-out
  multi-label ingestion is out of scope.
- **CVE patching, container sandbox, prompt-injection defense** — the PoC ran
  on an isolated dev machine; production must pin a patched CrewAI release,
  add `pip-audit` in CI, and add an injection-defense layer before untrusted
  body content reaches the summarizer.
- **Docker container, Kubernetes deployment, CronJob scheduling** — the PoC
  runs as a single-shot local CLI; container packaging, Kubernetes deployment,
  and scheduled invocation are production-scope.
- **Comprehensive PII/PCI redaction** — `redaction.py` masks email addresses
  only (a stub). Named entities (person/institution names) reproduced by the
  summarizer were **hand-redacted** in the demo; automated named-entity
  scrubbing is production-scope.
- **External idempotency claim** — only DB-layer `INSERT OR IGNORE` on
  `message_id` is in place; a Redis/Postgres claim around side-effecting tool
  calls is production-scope.
- **LLM cost governance** — no native cost cap, dollar limit, or circuit
  breaker; production must front the model with an LLM proxy (e.g.
  `litellm-proxy`) enforcing a per-project daily spend limit.
- **Real-time delivery** — Gmail Pub/Sub push + Workload Identity Federation
  is out of scope; ingestion is pull-based (batch by label).
- **Service Account + Domain-Wide Delegation** — the PoC uses the installed-app
  OAuth flow, not org-wide SA/DWD provisioning.
- **Postgres / pgvector persistence** — the PoC ships SQLite for both the
  summary vault and the DLQ; Postgres and pgvector-backed vector stores are
  production-scope.
- **KMS-managed secrets** — credentials live in gitignored local files, not a
  managed key store.
- **Audit logging** — no security-audit trail is emitted; telemetry is stderr
  logging only, without audit-grade retention.
- **Production observability** — no Phoenix/Arize tracing, no OpenTelemetry
  integration; structured observability platforms are deferred.
- **CI pipeline (GitHub Actions)** — the PoC's quality gate is local `pytest`,
  `ruff`, and `mypy --strict` via pre-commit; there is no CI workflow.
  Production is expected to add one.
- **Multi-framework benchmark** — this spike evaluates CrewAI against the
  requirements; it is not a head-to-head benchmark of LangGraph / Pydantic AI.

## Layout

```
src/mail_ingestor/
├── config.py            # Settings dataclass + .env loader (core)
├── schemas.py           # Pydantic boundary contracts (core)
├── redaction.py         # PII scrub — wired input-side (core)
├── llm.py               # Native Anthropic SDK factory — sole provider point (core)
├── gmail/               # OAuth, LabelResolver, reader, MIME parser (core)
├── persistence/         # SQLite vault + DLQ (core)
├── tools/
│   └── gmail_tool.py    # CrewAI BaseTool wrappers          ← fence file
├── flow.py              # CrewAI Flow orchestration          ← fence file
├── demo.py              # offline --demo batch (fixtures + canned LLM)
├── main.py              # CLI entrypoint (auth / --label / --demo)
└── telemetry.py         # stderr token/latency accounting
```

## Development

Install the hooks once (covers commit and push stages):

```bash
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

On every commit: whitespace / EOF / large-file / merge-conflict hygiene,
`detect-private-key`, `gitleaks` secret scan, `ruff` lint (`--fix`) +
`ruff format`, and the framework-fence pygrep hook. On every push: the full
`pytest` suite must pass.

```bash
uv run pytest                                # full suite
uv run ruff check src/ tests/                # lint
uv run --with mypy mypy --strict src/        # types
uv run pre-commit run --all-files            # everything CI does
```

## Notes

**No LiteLLM.** CrewAI 1.15.1's resolved dependency tree does not include
`litellm` (verified against `uv.lock`); the LLM call path uses the `anthropic`
SDK directly. The "native provider SDK" rule is satisfied by construction.

**Rate limits.** Seed tokens draw from the Claude subscription's usage quotas
(5-hour windows), not a pay-per-token Console budget. Smaller tiers (haiku)
have quotas separate from larger tiers, so rate-limiting on one tier does not
block another. The model is set by `LLM_MODEL` in `.env` (demo default
`claude-haiku-4-5-20251001`). Refresh an expired token by re-running
`claude setup-token`.

**Provider swap.** The provider abstraction lives in `llm.py`; no other module
imports the SDK. Swapping providers touches `llm.py`, `pyproject.toml`,
`.env.example`, and — because keyless auth ties `Settings.anthropic_auth_token`
to Anthropic semantics — `config.py`.

## References

In-repo:

- [findings.md](findings.md) — framework verdict, 4-gap evidence, recommendation
- [docs/live_demo_workflow.md](docs/live_demo_workflow.md) — full demo reproduction steps
- [demo/live-run/](demo/live-run/) — five redacted live-run samples (evidence for `findings.md`)
- [demo/](demo/) — five offline canned samples (`--demo` output, deterministic)

External (design docs live outside this repo, in the project's `base-docs/`
set / Linear — not committed here): **architecture.md** (§ decision-support
artifact discipline), **prd.md** (FR29 findings format, FR31/FR32 import fence,
FR33 decision-enablement, NFR-M3 reproducibility), **task.md**,
**sprint-plan.md**, and the **technical CrewAI–Gmail integration research**
(`technical-crewai-gmail-integration-poc-research-2026-07-07`, incl. Chapter 8
recommendations). `findings.md` links these at their `../base-docs/` paths.
