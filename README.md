# crewai-gmail-int-poc

PoC: read Gmail-labeled emails, summarize them via a CrewAI-orchestrated LLM, and
emit a structured record. Framework-selection spike for the Knowledge Vault.

**Status:** scaffold only (EFSP-328). No Gmail/CrewAI/LLM logic implemented yet.

## Setup (fresh machine)

Prerequisite (one-time): install uv — `curl -LsSf https://astral.sh/uv/install.sh | sh`

Then:

```bash
uv sync
uv run python -m mail_ingestor.main --help
```

Copy `.env.example` to `.env` and fill in values when logic tasks begin.

## Pre-commit guardrails

Install the hooks once (covers both commit and push stages):

```bash
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

On every commit: whitespace/EOF/large-file/merge-conflict hygiene, `detect-private-key`,
`gitleaks` secret scan, `ruff` lint (`--fix`) + `ruff format`, and `no-crewai-in-core`
(blocks a `crewai` import anywhere under `src/mail_ingestor/` except `crew/`). On every
push: the full `pytest` suite must pass.

Run all hooks manually against the whole repo:

```bash
uv run pre-commit run --all-files
```

## Layout

- `src/mail_ingestor/gmail/` — Gmail reader (core, framework-agnostic)
- `src/mail_ingestor/schemas.py` — `SummaryRecord` contract (core)
- `src/mail_ingestor/redaction.py` — PII redaction stub (core)
- `src/mail_ingestor/persistence/` — SQLite writer (core)
- `src/mail_ingestor/crew/` — CrewAI Agent/Task/Flow + Gmail tool wrapper
  (**the only package that imports crewai**)

## What's reusable / what isn't

The core modules (`gmail/`, `schemas.py`, `redaction.py`, `persistence/`) are plain
Python and port to another framework (LangGraph, Pydantic AI) in ~2 dev-days. Only
`crew/` carries CrewAI-specific choices. Discipline receipt:

```bash
grep -r "import crewai" src/    # returns nothing until crew/ is implemented
```

## Note on LiteLLM

Earlier CrewAI releases bundled `litellm` transitively. The pinned build here
(crewai 1.15.1) does **not** — `uv.lock` contains no litellm entry; CrewAI's resolved
tree uses `openai`, `instructor`, and `mcp`. The PoC's "no LiteLLM" rule is therefore
satisfied by construction, not by discipline. Our LLM call path uses the `anthropic`
SDK directly regardless.
