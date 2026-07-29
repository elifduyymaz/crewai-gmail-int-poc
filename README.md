# crewai-gmail-int-poc

A framework-selection spike: read Gmail-labeled emails, summarize each via a
CrewAI-orchestrated LLM, and emit a structured `SummaryRecord`. The core is
plain Python; CrewAI lives behind a strict two-file fence so the pipeline
ports cleanly to LangGraph or Pydantic AI if the framework decision lands
elsewhere.

## Status

Foundations and the Gmail ingest transport layer are complete. CrewAI Agent
orchestration and persistence are the next passes.

| Layer | Status |
|---|---|
| Package scaffold, pre-commit guardrails, boundary Pydantic schemas | shipped |
| Settings dataclass + `.env.example` (keyless auth) | shipped |
| Gmail OAuth installed-app flow + `auth` CLI | shipped |
| Label resolver (name → id, in-memory cache) | shipped |
| Gmail reader (list by label + get message) | shipped |
| MIME parser (text/plain, HTML fallback, multipart, attachments) | shipped |
| Native LLM provider abstraction (`llm.py`, Anthropic SDK, seed token) | shipped |
| CrewAI `BaseTool` wrappers for Gmail services | shipped |
| CrewAI `BaseLLM` adapter + Flow orchestration | pending |
| SQLite persistence (vault + DLQ) | pending |
| CLI batch loop + demo | pending |

Quality gates: **97 tests green**, `ruff` clean, `mypy --strict` clean on
new modules, pre-commit hooks pass.

## Quick start

Prerequisite (one-time): install `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Set up the project and run the Gmail OAuth flow:

```bash
uv sync
cp .env.example .env
uv run mail-ingestor auth
```

Before running LLM-touching code, obtain a Claude seed token in a separate
terminal:

```bash
claude setup-token
```

The command opens a browser OAuth flow tied to your Claude subscription and
prints an `sk-ant-oat01-...` value; paste it into `.env` as
`ANTHROPIC_AUTH_TOKEN`. This project does **not** use `ANTHROPIC_API_KEY`
— auth is keyless via the SDK's `auth_token` parameter.

## Layout

```
src/mail_ingestor/
├── config.py            # Settings dataclass + .env loader (core)
├── schemas.py           # Pydantic boundary contracts
│                        #   EmailMessage, Summary, SummaryRecord, DeadLetterRecord
├── redaction.py         # PII redaction stub
├── llm.py               # Native Anthropic SDK factory — sole provider point
├── gmail/
│   ├── auth.py          # OAuth installed-app flow
│   ├── labels.py        # LabelResolver (name → id, cached)
│   ├── reader.py        # GmailReaderService (list, get)
│   └── parser.py        # MIME → EmailMessage
├── tools/
│   └── gmail_tool.py    # CrewAI BaseTool wrappers          ← fence file
├── crew/                # CrewAI Agent / Task / Flow          ← fence dir
├── persistence/         # SQLite vault + DLQ (pending)
└── main.py              # CLI entrypoint (argparse)
```

Modules outside `crew/` and `tools/gmail_tool.py` are framework-agnostic:
plain Python with no CrewAI coupling.

## Architecture invariants

Two mechanical discipline rules keep the core reusable:

1. **`crewai` imports live only inside the two-file fence** — `crew/` and
   `tools/gmail_tool.py`. Enforced by the `no-crewai-in-core` pre-commit
   pygrep hook and by `tests/test_discipline.py`.
2. **`anthropic` is imported only in `llm.py`** — the sole provider
   abstraction. Enforced by the same discipline test.

Discipline receipt (returns only fence files):

```bash
grep -RE '^\s*(from|import)\s+crewai(\b|\.)' src/mail_ingestor/
grep -RE '^\s*(from|import)\s+anthropic(\b|\.)' src/mail_ingestor/
```

## Development

Install the hooks once (covers both commit and push stages):

```bash
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

On every commit: whitespace / EOF / large-file / merge-conflict hygiene,
`detect-private-key`, `gitleaks` secret scan, `ruff` lint (`--fix`) +
`ruff format`, and the framework-fence pygrep hook. On every push: the full
`pytest` suite must pass.

Common workflows:

```bash
uv run pytest                                # full suite
uv run ruff check src/ tests/                # lint
uv run ruff format src/ tests/               # format
uv run --with mypy mypy --strict src/        # types
uv run pre-commit run --all-files            # everything CI does
```

## Notes

**No LiteLLM.** CrewAI 1.15.1's resolved dependency tree does not include
`litellm` (verified against `uv.lock`); the LLM call path uses the
`anthropic` SDK directly. The PoC's "native provider SDK" rule is satisfied
by construction, not by discipline alone.

**Rate limits.** Seed tokens draw from the Claude subscription's usage
quotas (5-hour windows), not a pay-per-token Console budget. Smaller tiers
(haiku) have quotas separate from larger tiers (sonnet, opus), so
rate-limiting on one tier does not block another. Refresh an expired token
by re-running `claude setup-token`.

**Provider swap.** The provider abstraction lives in `llm.py`; no other
module imports the SDK. Swapping providers (Anthropic → OpenAI) touches
`llm.py`, `pyproject.toml`, `.env.example`, and — because the keyless-auth
decision ties `Settings.anthropic_auth_token` to Anthropic semantics —
`config.py`. The single-source-of-provider invariant is preserved
architecturally; the literal "one-file swap" target from the design doc is
superseded by the keyless-auth choice.

**Reusable core.** `gmail/`, `schemas.py`, `redaction.py`, `config.py`, and
`persistence/` are plain Python with no CrewAI coupling. If the framework
decision lands against CrewAI, these port to LangGraph or Pydantic AI in
an estimated ~2 dev-days — no throwaway code.
