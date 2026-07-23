# Pre-commit Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire a pre-commit guardrail layer (hygiene, ruff lint+format, secret scanning, and the framework-agnostic discipline invariant) plus `.gitignore` OS/editor additions onto the existing scaffold.

**Architecture:** A `.pre-commit-config.yaml` drives everything. Ruff runs through the project's own pinned ruff via `uv run` (version parity with CI, no drift). The framework-agnostic invariant is enforced two ways: a fast `pygrep` pre-commit hook AND a plain pytest so CI catches it without pre-commit. Secret scanning uses gitleaks. Pytest runs on the `pre-push` stage only, so commits stay fast.

**Tech Stack:** pre-commit, ruff (already pinned), gitleaks, pytest, uv.

## Global Constraints

- Base branch `dev`; feature branch `development/efsp-329-wire-pre-commit-guardrails-and-gitignore`; PR targets `dev`.
- Ruff invoked via `uv run ruff` (project's pinned ruff), NOT a bundled ruff-pre-commit binary — keeps hook/CI/manual parity.
- Discipline invariant: `import crewai` / `from crewai` is forbidden anywhere under `src/mail_ingestor/` EXCEPT `src/mail_ingestor/crew/`.
- pre-commit hooks pin explicit revisions (via `pre-commit autoupdate`).
- Pytest guardrail runs on `pre-push` stage only (never slows commits).
- `uv` is at `~/.local/bin`; run `export PATH="$HOME/.local/bin:$PATH"`. All tools via `uv run`.
- Environment facts (probed): no system `go`, no `gitleaks` binary, `brew` present at `/opt/homebrew/bin/brew`, GitHub reachable.
- Do not commit secrets, `.venv/`, caches, or `.pyc`.

---

## File Structure

- `.pre-commit-config.yaml` — Create. All hook definitions.
- `pyproject.toml` — Modify. Add `pre-commit` to dev deps (via `uv add --dev`).
- `uv.lock` — Modify (by uv).
- `tests/test_discipline.py` — Create. The Python-level invariant guard.
- `.gitignore` — Modify. Append OS/editor section.
- `README.md` — Modify. Add "Pre-commit guardrails" section.

---

### Task 1: pre-commit dependency + config (hygiene, ruff, discipline, pre-push pytest)

Add pre-commit and land the config for everything EXCEPT gitleaks (Task 3), and get `pre-commit run --all-files` green.

**Files:**
- Modify: `pyproject.toml`, `uv.lock`
- Create: `.pre-commit-config.yaml`

**Interfaces:**
- Consumes: the EFSP-328 scaffold on `dev` (pyproject with ruff, `src/mail_ingestor/`, `tests/`).
- Produces: a working `.pre-commit-config.yaml` (hooks: pre-commit-hooks hygiene, local ruff-check, local ruff-format, local no-crewai-in-core, local pytest pre-push); `uv run pre-commit` available.

- [ ] **Step 1: Add pre-commit to dev deps**

Run:
```bash
export PATH="$HOME/.local/bin:$PATH"
uv add --dev pre-commit
uv sync
```
Expected: `pre-commit` appears under `[dependency-groups].dev` in `pyproject.toml`; `uv.lock` updated; `uv run pre-commit --version` prints a version.

- [ ] **Step 2: Write `.pre-commit-config.yaml`**

Create `.pre-commit-config.yaml`:

```yaml
# Guardrails for the CrewAI Gmail-ingestion PoC (EFSP-329).
# Install: uv run pre-commit install --hook-type pre-commit --hook-type pre-push
minimum_pre_commit_version: "3.5.0"

repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-merge-conflict
      - id: check-case-conflict
      - id: check-toml
      - id: check-yaml
      - id: detect-private-key
      - id: check-added-large-files
        args: ["--maxkb=1024"]

  - repo: local
    hooks:
      - id: ruff-check
        name: ruff check (project ruff via uv)
        entry: uv run ruff check --fix
        language: system
        types: [python]
        require_serial: true

      - id: ruff-format
        name: ruff format (project ruff via uv)
        entry: uv run ruff format
        language: system
        types: [python]
        require_serial: true

      - id: no-crewai-in-core
        name: no crewai import outside crew/
        description: Core modules must stay framework-agnostic; crewai only under crew/.
        entry: '(?m)^\s*(from|import)\s+crewai(\b|\.)'
        language: pygrep
        files: '^src/mail_ingestor/'
        exclude: '^src/mail_ingestor/crew/'

      - id: pytest-on-push
        name: pytest (pre-push guardrail)
        entry: uv run pytest
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-push]
```

- [ ] **Step 3: Normalize formatting once**

Run: `uv run ruff format`
Expected: existing files reported as already formatted or reformatted with a count. This ensures the `ruff-format` hook won't fail the first `--all-files` run.

- [ ] **Step 4: Pin hook revisions**

Run: `uv run pre-commit autoupdate`
Expected: the `pre-commit-hooks` `rev:` is updated to the latest tag (may stay `v5.0.0` or bump). Local hooks are unaffected.

- [ ] **Step 5: Run all hooks and make them green**

Run: `uv run pre-commit run --all-files`
Expected: PASS for every hook. `end-of-file-fixer`/`trailing-whitespace` may modify tracked files (e.g. docs) on first run — if so, they report "Fixed"; re-run until the run is clean:
```bash
uv run pre-commit run --all-files
```
Second run Expected: all hooks `Passed`.

If any hygiene hook keeps modifying a file, `git add -A` the fixes and re-run. Do NOT disable a hook to make it pass.

- [ ] **Step 6: Commit**

```bash
git add .pre-commit-config.yaml pyproject.toml uv.lock
# include any files the hygiene hooks normalized:
git add -A
git commit -m "build: add pre-commit config (hygiene, ruff, discipline, pre-push pytest) (EFSP-329)"
```

---

### Task 2: Python-level discipline guard test

Reinforce the framework-agnostic invariant as a pytest so CI enforces it even without pre-commit, and prove both the test and the hook actually fire on a violation.

**Files:**
- Create: `tests/test_discipline.py`

**Interfaces:**
- Consumes: `src/mail_ingestor/` package tree; the `no-crewai-in-core` hook from Task 1.
- Produces: `tests/test_discipline.py::test_no_crewai_import_in_core`.

- [ ] **Step 1: Write the guard test**

Create `tests/test_discipline.py`:

```python
"""Framework-agnostic discipline: crewai must not leak into core modules.

Mirrors the `no-crewai-in-core` pre-commit hook so the invariant is enforced in
CI even when pre-commit is not installed.
"""

from __future__ import annotations

import re
from pathlib import Path

# Same intent as the pygrep hook entry: a real import of crewai at line start.
_CREWAI_IMPORT = re.compile(r"^\s*(?:from|import)\s+crewai(?:\b|\.)", re.MULTILINE)

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "src" / "mail_ingestor"
_CREW_DIR = _PACKAGE_ROOT / "crew"


def _core_python_files() -> list[Path]:
    return [
        p
        for p in _PACKAGE_ROOT.rglob("*.py")
        if _CREW_DIR not in p.parents and p != _CREW_DIR
    ]


def test_no_crewai_import_in_core() -> None:
    offenders = [
        str(p.relative_to(_PACKAGE_ROOT))
        for p in _core_python_files()
        if _CREWAI_IMPORT.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "crewai imported in framework-agnostic core (allowed only under crew/): "
        f"{offenders}"
    )


def test_core_files_are_actually_scanned() -> None:
    # Guards against the invariant silently passing because no files were found.
    scanned = {p.name for p in _core_python_files()}
    assert {"schemas.py", "redaction.py"} <= scanned
```

- [ ] **Step 2: Run it — expect PASS (scaffold is clean)**

Run: `uv run pytest tests/test_discipline.py -v`
Expected: 2 passed (`test_no_crewai_import_in_core`, `test_core_files_are_actually_scanned`).

- [ ] **Step 3: Negative verification — inject a violation, confirm BOTH test and hook fail**

Temporarily add a crewai import to a core module:
```bash
printf '\nimport crewai  # TEMP violation\n' >> src/mail_ingestor/schemas.py
uv run pytest tests/test_discipline.py::test_no_crewai_import_in_core -v ; echo "pytest_exit=$?"
uv run pre-commit run no-crewai-in-core --all-files ; echo "hook_exit=$?"
```
Expected: pytest FAILS (`pytest_exit=1`) naming `schemas.py`; the hook FAILS (`hook_exit=1`). Capture this output for the report — it is the proof the guardrail works.

Now revert the violation:
```bash
git checkout -- src/mail_ingestor/schemas.py
uv run pytest tests/test_discipline.py -v
```
Expected: file restored; 2 passed again. Confirm `git status` shows no change to `schemas.py`.

- [ ] **Step 4: Commit (only the test)**

```bash
git add tests/test_discipline.py
git commit -m "test: add framework-agnostic discipline guard (no crewai in core) (EFSP-329)"
```

---

### Task 3: gitleaks secret-scanning hook

Add gitleaks. The preferred path is the pinned upstream pre-commit hook; a documented fallback covers this machine's missing Go toolchain.

**Files:**
- Modify: `.pre-commit-config.yaml`

**Interfaces:**
- Consumes: the config from Task 1.
- Produces: a working `gitleaks` hook; `uv run pre-commit run gitleaks --all-files` passes clean.

- [ ] **Step 1: Add the upstream gitleaks hook (preferred path)**

Add this repo block to `.pre-commit-config.yaml` (after the `pre-commit-hooks` repo, before `repo: local`):

```yaml
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.21.2
    hooks:
      - id: gitleaks
```

Then pin its rev: `uv run pre-commit autoupdate --repo https://github.com/gitleaks/gitleaks`

- [ ] **Step 2: Try to run it**

Run: `uv run pre-commit run gitleaks --all-files ; echo "exit=$?"`
Expected (success): `Passed`, `exit=0`.

- [ ] **Step 3: If Step 2 fails to BUILD (missing Go toolchain), use the fallback**

Only if Step 2 errored on installing/building the hook (e.g. a Go/golang toolchain error), NOT if it ran and found secrets:

Install the gitleaks binary and switch this hook to a local system hook:
```bash
brew install gitleaks
gitleaks version
```
Replace the upstream gitleaks repo block from Step 1 with a local hook (place under `repo: local`, alongside the others):

```yaml
      - id: gitleaks
        name: gitleaks (secret scan, system binary)
        entry: gitleaks git --pre-commit --redact --staged --verbose
        language: system
        pass_filenames: false
```

Note in your report that the fallback (brew + system hook) was used and why, so the README can document the `brew install gitleaks` prerequisite.

- [ ] **Step 4: Verify clean on the whole repo**

Run: `uv run pre-commit run gitleaks --all-files ; echo "exit=$?"`
Expected: `Passed`, `exit=0` (no secrets — `.env.example` has empty values, `.env` is gitignored).

If gitleaks flags a false positive, do NOT delete the finding blindly — report it; a real leak must be surfaced, not suppressed.

- [ ] **Step 5: Commit**

```bash
git add .pre-commit-config.yaml
git commit -m "build: add gitleaks secret-scanning hook (EFSP-329)"
```

---

### Task 4: .gitignore additions, README, hook install, final DoD

**Files:**
- Modify: `.gitignore`, `README.md`

**Interfaces:**
- Consumes: the full config from Tasks 1–3.
- Produces: documented, installed guardrails; verified definition of done.

- [ ] **Step 1: Append OS/editor section to `.gitignore`**

Append to `.gitignore`:

```gitignore

# OS / editor
.DS_Store
.idea/
.vscode/
*.swp
```

- [ ] **Step 2: Add the README section**

Add this section to `README.md` (after the Setup section):

```markdown
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
```

(If Task 3 used the gitleaks fallback, add a line: "Requires the `gitleaks` CLI —
`brew install gitleaks`.")

- [ ] **Step 3: Install the hooks**

Run: `uv run pre-commit install --hook-type pre-commit --hook-type pre-push`
Expected: "pre-commit installed at .git/hooks/pre-commit" and ".git/hooks/pre-push".

- [ ] **Step 4: Full definition-of-done verification**

Run each and confirm:
```bash
uv run pre-commit run --all-files            # every hook Passed
uv run pytest -v                             # all pass incl. test_discipline
uv run ruff check src tests                  # clean
uv run ruff format --check src tests         # already formatted
grep -rn "crewai" src/mail_ingestor --include=*.py | grep -v '/crew/' | grep -E '^\S+:\s*(import|from)\s+crewai' ; echo "core_import_exit=$?"
```
Expected: all hooks pass; pytest green; ruff clean; format check passes; the final grep prints nothing and `core_import_exit=1` (no crewai import in core).

- [ ] **Step 5: Commit**

```bash
git add .gitignore README.md
git commit -m "docs: gitignore OS/editor entries + pre-commit README section (EFSP-329)"
```

---

## Self-Review

**Spec coverage:**
- pre-commit config with hygiene + ruff lint/format + discipline hook + gitleaks → Tasks 1, 3. ✓
- Discipline hook (pygrep, scoped to core, exempt crew/) → Task 1 config. ✓
- Python-level invariant reinforcement + negative proof → Task 2. ✓
- gitleaks secret scanning (with env fallback) → Task 3. ✓
- pre-push pytest guardrail → Task 1 config. ✓
- pre-commit added to dev deps + lock → Task 1. ✓
- `.gitignore` OS/editor additions → Task 4. ✓
- README "Pre-commit guardrails" section + install command → Task 4. ✓
- DoD (all hooks green, injected violation fails, gitleaks clean, README documents install) → Tasks 2 (negative proof) + 4 (final). ✓

**Placeholder scan:** No TBD/TODO. The gitleaks fallback in Task 3 is a named, triggered decision (missing Go toolchain), not vague hand-waving. ✓

**Type/consistency check:** The pygrep hook regex `(?m)^\s*(from|import)\s+crewai(\b|\.)` and the pytest regex `^\s*(?:from|import)\s+crewai(?:\b|\.)` express the same invariant. The `files`/`exclude` scoping in the hook matches the `_CREW_DIR` exclusion in the test. Hook ids referenced in later tasks (`no-crewai-in-core`, `gitleaks`) match their definitions. ✓
