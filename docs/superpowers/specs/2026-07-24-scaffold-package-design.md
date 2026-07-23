# EFSP-328 — Scaffold Python Package with Pinned Dependencies

**Date:** 2026-07-24
**Task:** EFSP-328 "1.1 Scaffold Python package with pinned dependencies"
**Related PRD:** CrewAI Gmail-ingestion PoC (Knowledge Vault framework-selection spike)

## Purpose

Stand up a reproducible, framework-agnostic Python package skeleton for the CrewAI
Gmail-ingestion PoC. This is task 1.1 of the PoC — the foundation only. No Gmail,
CrewAI, LLM, or persistence *logic* is written here; those land in later tasks. The
deliverable is a clean, installable, testable skeleton with the full dependency set
pinned so every later task builds on a reproducible environment.

## Decisions (locked)

- **Scope:** Skeleton only. Package layout, pinned dependencies, tooling config, README,
  one smoke test. No business logic.
- **Toolchain:** `uv` (installed as part of this task) managing a pinned **Python 3.11**.
  System Python is 3.9.6, which is below CrewAI 1.15.x's 3.10+ requirement, so a managed
  3.11 is a hard prerequisite.
- **LLM provider:** Anthropic Claude. Pin the `anthropic` native SDK; default model is a
  Claude Sonnet. No LiteLLM in our call path (see the litellm note below).

## Package Layout (src-layout)

```
crewai-gmail-int-poc/
├── pyproject.toml          # pinned deps, project metadata, tool config (ruff, pytest)
├── .python-version         # 3.11 (uv-pinned)
├── uv.lock                 # committed lockfile → reproducible installs
├── .gitignore              # python + .env + OAuth secrets + *.db
├── .env.example            # ANTHROPIC_API_KEY, Gmail credential/token paths
├── README.md               # <=3-command setup + "what's reusable / what isn't" section
├── docs/superpowers/specs/ # this design doc
├── src/
│   └── mail_ingestor/
│       ├── __init__.py     # __version__ = "0.1.0"
│       ├── main.py         # CLI entrypoint stub (argparse --help works; no logic)
│       ├── config.py       # env-loading stub
│       ├── schemas.py      # module stub -> SummaryRecord lands in a later task
│       ├── redaction.py    # module stub -> PII redaction stub lands later  (CORE)
│       ├── gmail/
│       │   └── __init__.py # reader.py lands later                          (CORE)
│       ├── persistence/
│       │   └── __init__.py # sqlite persistence lands later                 (CORE)
│       └── crew/
│           └── __init__.py # flow.py + tools/ land later (only place crewai imports)
└── tests/
    └── test_smoke.py       # asserts package imports and __version__ is set
```

Each stub module carries a one-line docstring naming the task that will fill it, so
nothing is silently empty and the intended boundaries are visible from day one.

### Framework-agnostic discipline

The PRD requires `import crewai` to appear in exactly two files eventually (the tool
wrapper and the flow orchestration), both under `src/mail_ingestor/crew/`. The core
modules — `schemas.py`, `redaction.py`, `gmail/`, `persistence/` — must never import
crewai. This scaffold establishes that boundary: at scaffold time
`grep -r "import crewai" src/` returns nothing, and the only directory reserved for
crewai code is `crew/`.

## Pinned Dependencies (`pyproject.toml`)

Runtime:
- `crewai==1.15.1` (exact pin per PRD)
- `pydantic>=2`
- `anthropic` (native SDK; the PoC LLM call path)
- `google-api-python-client`, `google-auth-oauthlib`, `google-auth-httplib2` (Gmail API)
- `python-dotenv` (env loading)

Dev:
- `pytest`, `pytest-cov`, `ruff`

All versions are locked in `uv.lock` (committed). Exact-version resolution comes from the
lockfile; the `pyproject.toml` constraints above are the declared minimums, and `uv.lock`
freezes the concrete versions for reproducibility.

### LiteLLM note (PoC finding — updated at Task 2)

The plan originally assumed CrewAI pulls in `litellm` transitively. **The pinned build
does not.** Task 2's `uv.lock` (crewai 1.15.1 on Python 3.11, 157 packages) contains **no
`litellm` entry** — CrewAI's resolved tree uses `openai`, `instructor`, and `mcp` instead.
This was independently verified against the lockfile. So the PRD's "no LiteLLM" constraint
is satisfied by construction here, not by discipline: there is nothing to strip. Our LLM
call path will still use the `anthropic` SDK directly. This is a genuine framework-behavior
finding for the PoC assessment (CrewAI 1.15.x no longer bundles litellm).

## Toolchain Setup Steps

1. Install `uv`: `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. `uv init` (adapt to src-layout), `uv python pin 3.11`
3. `uv add` runtime deps; `uv add --dev` dev deps
4. `uv sync` → generates and commits `uv.lock`
5. `uv run pytest` → smoke test passes

## README Requirements

- Setup reproducible in **<=3 commands** on a fresh machine (uv install is a one-time
  prerequisite; `uv sync` + `uv run ...` is the 2-command steady state).
- A "What's reusable / What isn't" section: the core modules port to another framework
  (~2 dev-days per PRD); the `crew/` layer is where CrewAI-specific choices live.
- A note on the framework-agnostic discipline and the `grep -r "import crewai" src/`
  receipt.

## Definition of Done

- `uv sync` succeeds on a clean checkout.
- `uv run python -m mail_ingestor.main --help` prints usage and exits 0.
- `uv run pytest` passes the smoke test.
- `grep -r "import crewai" src/` returns nothing (clean discipline baseline).
- `uv run ruff check src tests` passes.
- `.env`, OAuth `credentials.json` / `token.json`, and `*.db` are gitignored.

## Out of Scope (later tasks)

- `SummaryRecord` Pydantic model and its fields
- Gmail reader service and MIME parsing
- CrewAI Agent / Task / Flow orchestration and the Gmail tool wrapper
- SQLite persistence
- PII redaction logic
- Any LLM invocation
