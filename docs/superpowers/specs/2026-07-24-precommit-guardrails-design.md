# EFSP-329 — Wire Pre-commit Guardrails and Gitignore

**Date:** 2026-07-24
**Task:** EFSP-329 "1.2 Wire pre-commit guardrails and gitignore"
**Base branch:** `dev` (already contains the EFSP-328 scaffold)
**Feature branch:** `development/efsp-329-wire-pre-commit-guardrails-and-gitignore`
**PR target:** `dev`

## Purpose

Add an automated guardrail layer to the scaffold so discipline is enforced at commit
time, not just by review. Two deliverables: a `.pre-commit-config.yaml` wiring lint,
format, hygiene, secret-scanning, and the PoC's framework-agnostic invariant; and
`.gitignore` additions for OS/editor noise. The framework-agnostic invariant is also
reinforced as a plain pytest so CI enforces it without depending on pre-commit.

## Decisions (locked)

- **Secret scanning:** gitleaks (fintech repo; low false positives; no baseline file).
- **Discipline hook:** yes — block `import crewai` / `from crewai` anywhere under
  `src/mail_ingestor/` except `crew/`.
- **Formatting:** yes — enforce both `ruff` lint (`--fix`) and `ruff-format`.

## `.pre-commit-config.yaml`

Pinned hook revisions (pre-commit best practice). Hooks:

1. **`pre-commit/pre-commit-hooks`** — hygiene:
   `trailing-whitespace`, `end-of-file-fixer`, `check-added-large-files`,
   `check-merge-conflict`, `check-toml`, `check-yaml`, `check-case-conflict`,
   `detect-private-key`.
2. **ruff** — `ruff` (lint, `--fix`) and `ruff-format`. (Implementation note: the plan
   overrides this to LOCAL hooks invoking the project's pinned ruff via `uv run ruff`,
   rather than the `astral-sh/ruff-pre-commit` repo, to keep hook/CI/manual version parity.
   This is what shipped.)
3. **`gitleaks/gitleaks`** — `gitleaks` secret/entropy scan over the diff.
4. **Local `no-crewai-in-core`** — the discipline hook:
   - `language: pygrep`
   - `entry: '(?m)^\s*(from|import)\s+crewai(\b|\.)'`
   - `files: '^src/mail_ingestor/'`
   - `exclude: '^src/mail_ingestor/crew/'`
   - Fails the commit if any core file imports crewai; `crew/` is exempt.
5. **Local `pytest` on the `pre-push` stage** — `uv run pytest`:
   - `stages: [pre-push]` so it does NOT slow down commits, only pushes.
   - Blocks pushing a red suite.

## Python-level reinforcement

`tests/test_discipline.py::test_no_crewai_import_in_core` — scans every `.py` under
`src/mail_ingestor/` excluding `crew/`, and asserts none contain a `crewai` import
(matched with the same anchored regex the hook uses). This makes the invariant
enforceable in CI without pre-commit installed. It passes at scaffold state; the plan
demonstrates it FAILS on an injected violation (reverted, not committed).

## Supporting changes

- **`pyproject.toml`:** add `pre-commit` to `[dependency-groups].dev` so
  `uv run pre-commit` is reproducible and locked.
- **`.gitignore`:** append an OS/editor section — `.DS_Store`, `.idea/`, `.vscode/`,
  `*.swp`. (Secrets, `.venv`, `.pyc`, caches already covered by EFSP-328.)
- **`README.md`:** add a "Pre-commit guardrails" section documenting
  `uv run pre-commit install --hook-type pre-commit --hook-type pre-push` and listing
  the guardrails.

## Definition of Done

- `uv run pre-commit run --all-files` passes clean (run `ruff format` once first so
  existing files are already formatted).
- Injecting `import crewai` into a core module (e.g. `schemas.py`) makes the
  `no-crewai-in-core` hook FAIL; removing it restores green. (Demonstrated, reverted.)
- `uv run pytest` passes, now including `test_no_crewai_import_in_core`.
- gitleaks runs clean; `detect-private-key` is active.
- `README.md` documents the install command.
- `pre-commit` present in dev deps and in `uv.lock`.

## Out of Scope

- GitHub Actions / CI pipeline (separate task).
- Production secret management (KMS, Secret Manager) — production-scope per PRD.
- mypy / type-checking hook (not in the scaffold's toolchain; a later decision).
