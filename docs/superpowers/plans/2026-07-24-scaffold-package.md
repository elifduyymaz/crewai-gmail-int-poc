# Scaffold Python Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a reproducible, framework-agnostic `mail_ingestor` Python package skeleton with pinned dependencies, uv toolchain, and a passing smoke test — no business logic.

**Architecture:** src-layout package managed by `uv` on a pinned Python 3.11. Core modules (`schemas`, `redaction`, `gmail/`, `persistence/`) are plain-Python placeholders that must never import crewai; a reserved `crew/` package is the only future home for crewai code. Dependencies are declared in `pyproject.toml` and frozen in a committed `uv.lock`.

**Tech Stack:** Python 3.11, uv, CrewAI 1.15.1, Pydantic v2, Anthropic SDK, google-api-python-client, pytest, ruff.

## Global Constraints

- Python interpreter: **3.11**, uv-managed (system 3.9.6 is too old for CrewAI).
- Package manager: **uv**. Setup reproducible in **≤3 commands** on a fresh machine.
- Pinned runtime deps: `crewai==1.15.1`, `pydantic>=2`, `anthropic`, `google-api-python-client`, `google-auth-oauthlib`, `google-auth-httplib2`, `python-dotenv`.
- Dev deps: `pytest`, `pytest-cov`, `ruff`.
- LLM call path uses the **Anthropic native SDK** — no LiteLLM in our code (CrewAI's transitive litellm dep is allowed and documented, not stripped).
- Framework-agnostic discipline: `grep -r "import crewai" src/` returns **nothing** at scaffold time; crewai is reserved to `src/mail_ingestor/crew/`.
- Secrets gitignored: `.env`, `credentials.json`, `token.json`, `*.db`.
- All work on branch `merveinan/efsp-328-scaffold-python-package`.

---

## File Structure

- `pyproject.toml` — project metadata, pinned deps, ruff + pytest config.
- `.python-version` — `3.11` (written by `uv python pin`).
- `uv.lock` — committed lockfile.
- `.gitignore` — python + secrets + db.
- `.env.example` — env var template.
- `README.md` — ≤3-command setup, reusability section, discipline note.
- `src/mail_ingestor/__init__.py` — `__version__`.
- `src/mail_ingestor/main.py` — argparse CLI entrypoint stub.
- `src/mail_ingestor/config.py` — env-loading stub.
- `src/mail_ingestor/schemas.py` — stub (SummaryRecord later).
- `src/mail_ingestor/redaction.py` — stub (PII redaction later).
- `src/mail_ingestor/gmail/__init__.py` — stub (reader later).
- `src/mail_ingestor/persistence/__init__.py` — stub (sqlite later).
- `src/mail_ingestor/crew/__init__.py` — stub (crewai flow/tools later).
- `tests/test_smoke.py` — import + version + CLI smoke tests.

---

### Task 1: Toolchain + project init

Install uv, pin Python 3.11, and create the bare uv project so later tasks have a working environment.

**Files:**
- Create: `pyproject.toml` (via `uv init`, then edited in Task 2)
- Create: `.python-version`

**Interfaces:**
- Consumes: nothing.
- Produces: a uv-managed project rooted at repo root, Python 3.11 pinned, importable `mail_ingestor` package path reserved under `src/`.

- [ ] **Step 1: Install uv (skip if already present)**

Run: `command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh`
Then ensure it's on PATH for this shell: `export PATH="$HOME/.local/bin:$PATH"`
Verify: `uv --version`
Expected: prints a version like `uv 0.x.y`.

- [ ] **Step 2: Initialize the uv project as a package (src-layout)**

Run from repo root: `uv init --package --name mail_ingestor --no-workspace`
This creates `pyproject.toml` and a `src/mail_ingestor/` skeleton. If `uv init` creates a sample module or `hello()` function, that's fine — Task 3 overwrites the package contents.
Expected: `pyproject.toml` exists; `src/mail_ingestor/` exists.

- [ ] **Step 3: Pin Python 3.11**

Run: `uv python pin 3.11`
Expected: `.python-version` created containing `3.11`; uv downloads a managed 3.11 if not present.

- [ ] **Step 4: Verify the interpreter resolves**

Run: `uv run python --version`
Expected: `Python 3.11.x`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .python-version
git commit -m "chore: init uv project on python 3.11 (EFSP-328)"
```

---

### Task 2: Pin dependencies and lock

Declare the full runtime + dev dependency set and freeze it. This task carries the `crewai==1.15.1` resolution risk — if that exact version does not resolve for Python 3.11, STOP and report rather than substituting a version.

**Files:**
- Modify: `pyproject.toml`
- Create: `uv.lock`

**Interfaces:**
- Consumes: uv project from Task 1.
- Produces: a resolved, locked environment; `uv sync` reproduces it exactly. Console script `mail-ingestor` maps to `mail_ingestor.main:main`.

- [ ] **Step 1: Add runtime dependencies**

Run:
```bash
uv add "crewai==1.15.1" "pydantic>=2" anthropic \
  google-api-python-client google-auth-oauthlib google-auth-httplib2 python-dotenv
```
Expected: resolution succeeds; deps appear under `[project].dependencies` in `pyproject.toml`; `uv.lock` is written.

**If `crewai==1.15.1` fails to resolve:** do not pick another version. Stop, capture the resolver error, and report it — the pin comes from the PRD and needs a human decision.

- [ ] **Step 2: Add dev dependencies**

Run: `uv add --dev pytest pytest-cov ruff`
Expected: deps appear under the dev dependency group; `uv.lock` updated.

- [ ] **Step 3: Add tool config + console script to `pyproject.toml`**

Ensure `pyproject.toml` contains these blocks (merge, don't duplicate existing keys):

```toml
[project.scripts]
mail-ingestor = "mail_ingestor.main:main"

[tool.ruff]
line-length = 100
src = ["src", "tests"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 4: Sync and verify the lock is consistent**

Run: `uv sync`
Expected: environment installs from `uv.lock` with no errors.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: pin runtime + dev dependencies and lock (EFSP-328)"
```

---

### Task 3: Package skeleton + failing smoke test

Replace whatever `uv init` scaffolded with the real module layout, and drive it via a smoke test written first (TDD).

**Files:**
- Create/Modify: `src/mail_ingestor/__init__.py`
- Create: `src/mail_ingestor/main.py`, `config.py`, `schemas.py`, `redaction.py`
- Create: `src/mail_ingestor/gmail/__init__.py`, `persistence/__init__.py`, `crew/__init__.py`
- Create: `tests/test_smoke.py`

**Interfaces:**
- Consumes: locked env from Task 2.
- Produces:
  - `mail_ingestor.__version__: str` (= `"0.1.0"`)
  - `mail_ingestor.main.build_parser() -> argparse.ArgumentParser`
  - `mail_ingestor.main.main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing smoke test**

Create `tests/test_smoke.py`:

```python
import subprocess
import sys

import mail_ingestor
from mail_ingestor.main import build_parser, main


def test_version_is_set():
    assert isinstance(mail_ingestor.__version__, str)
    assert mail_ingestor.__version__ == "0.1.0"


def test_parser_builds():
    parser = build_parser()
    args = parser.parse_args(["--label", "poc/reports"])
    assert args.label == "poc/reports"


def test_main_help_exits_zero():
    result = subprocess.run(
        [sys.executable, "-m", "mail_ingestor.main", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` for `mail_ingestor.main` (or a leftover `uv init` module without `build_parser`).

- [ ] **Step 3: Write `__init__.py`**

Create/overwrite `src/mail_ingestor/__init__.py`:

```python
"""mail_ingestor — CrewAI Gmail-ingestion PoC (framework-agnostic core)."""

__version__ = "0.1.0"
```

- [ ] **Step 4: Write the CLI entrypoint stub**

Create `src/mail_ingestor/main.py`:

```python
"""CLI entrypoint. Pipeline wiring lands in a later task — this is a stub."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mail-ingestor",
        description="Summarize Gmail-labeled emails into structured records (PoC).",
    )
    parser.add_argument(
        "--label",
        default="poc/reports",
        help="Gmail label to read messages from.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"[mail-ingestor] scaffold OK. Target label: {args.label!r}")
    print("[mail-ingestor] pipeline not implemented yet (scaffold task EFSP-328).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Write the core stub modules**

Create `src/mail_ingestor/config.py`:

```python
"""Environment/config loading. Implementation lands in a later task."""
```

Create `src/mail_ingestor/schemas.py`:

```python
"""Data contracts. SummaryRecord (Pydantic) lands in a later task."""
```

Create `src/mail_ingestor/redaction.py`:

```python
"""PII/PCI redaction stub. Framework-agnostic core — must not import crewai."""
```

Create `src/mail_ingestor/gmail/__init__.py`:

```python
"""Gmail reader package. GmailReaderService lands in a later task.

Framework-agnostic core — must not import crewai.
"""
```

Create `src/mail_ingestor/persistence/__init__.py`:

```python
"""Persistence package. SQLite writer lands in a later task.

Framework-agnostic core — must not import crewai.
"""
```

Create `src/mail_ingestor/crew/__init__.py`:

```python
"""CrewAI orchestration package. Agent/Task/Flow and the Gmail tool wrapper land
in later tasks. This is the ONLY package permitted to import crewai.
"""
```

- [ ] **Step 6: Remove any leftover `uv init` sample module**

If `uv init` left a sample file (e.g. `src/mail_ingestor/__init__.py` with a `main()`/`hello()` sample, or a stray module), ensure the files now match the contents above. Confirm no sample remains:
Run: `ls src/mail_ingestor`
Expected: exactly `__init__.py  config.py  crew  gmail  main.py  persistence  redaction.py  schemas.py`.

- [ ] **Step 7: Run the smoke test to verify it passes**

Run: `uv run pytest tests/test_smoke.py -v`
Expected: 3 passed.

- [ ] **Step 8: Commit**

```bash
git add src tests
git commit -m "feat: scaffold mail_ingestor package + smoke test (EFSP-328)"
```

---

### Task 4: Project hygiene files (gitignore, env example, README)

Add the supporting files that make the scaffold reproducible and self-documenting.

**Files:**
- Create: `.gitignore`, `.env.example`, `README.md`

**Interfaces:**
- Consumes: package + deps from Tasks 1–3.
- Produces: reproducible-setup documentation and secret-ignoring rules. No code interface.

- [ ] **Step 1: Write `.gitignore`**

Create `.gitignore`:

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.ruff_cache/
.coverage
htmlcov/

# Virtual env
.venv/

# Secrets / credentials
.env
credentials.json
token.json

# Local data
*.db
*.sqlite3
```

- [ ] **Step 2: Write `.env.example`**

Create `.env.example`:

```dotenv
# Anthropic (PoC LLM call path)
ANTHROPIC_API_KEY=

# Gmail OAuth (installed-app flow). Paths to gitignored credential files.
GMAIL_CREDENTIALS_PATH=credentials.json
GMAIL_TOKEN_PATH=token.json
```

- [ ] **Step 3: Write `README.md`**

Create `README.md`:

```markdown
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

CrewAI depends on `litellm` transitively. The PoC's "no LiteLLM" rule applies to our
own LLM call path — we call the `anthropic` SDK directly. We do not strip the
transitive dependency.
```

- [ ] **Step 4: Verify the full definition-of-done**

Run each and confirm:
```bash
uv sync                                            # succeeds
uv run python -m mail_ingestor.main --help         # prints usage, exit 0
uv run pytest -v                                   # all pass
uv run ruff check src tests                        # passes
grep -r "import crewai" src/ ; echo "exit=$?"      # no matches, exit=1
```
Expected: all green; grep prints nothing and reports `exit=1`.

- [ ] **Step 5: Commit**

```bash
git add .gitignore .env.example README.md
git commit -m "docs: add gitignore, env example, and README (EFSP-328)"
```

---

## Self-Review

**Spec coverage:**
- Package layout → Task 3. ✓
- Pinned deps + uv.lock → Task 2. ✓
- uv + Python 3.11 toolchain → Task 1. ✓
- Anthropic provider pin → Task 2 (Step 1). ✓
- Framework-agnostic discipline / crew-only crewai → Task 3 stubs + Task 4 DoD grep. ✓
- LiteLLM note → Task 4 README + spec. ✓
- ≤3-command setup → Task 4 README. ✓
- Secrets gitignored → Task 4 `.gitignore`. ✓
- Smoke test → Task 3. ✓
- ruff config → Task 2; ruff check → Task 4 DoD. ✓
- DoD (all commands green) → Task 4 Step 4. ✓

**Placeholder scan:** No TBD/TODO-as-work; module docstrings intentionally state "lands in a later task" (that is the deliverable of a scaffold, not a plan gap). ✓

**Type consistency:** `build_parser() -> ArgumentParser` and `main(argv) -> int` defined in Task 3 interfaces and used consistently in the test and implementation. `__version__ == "0.1.0"` consistent between test and `__init__.py`. ✓
