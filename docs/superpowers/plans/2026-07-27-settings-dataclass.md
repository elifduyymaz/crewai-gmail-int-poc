# Settings Dataclass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Define an immutable, framework-agnostic `Settings` dataclass loaded from the environment (LLM config only) and expand `.env.example`, driven by tests.

**Architecture:** A stdlib `@dataclass(frozen=True, slots=True)` in `src/mail_ingestor/config.py` with a `from_env()` classmethod using `python-dotenv`. The API key is `repr=False` (never logged). Missing required vars fail fast with a typed error.

**Tech Stack:** Python 3.11, stdlib dataclasses, python-dotenv, pytest.

## Global Constraints

- LLM-only scope: fields `anthropic_api_key` (required) and `llm_model` (default `claude-sonnet-5`). No Gmail/db/limit settings this task.
- `src/mail_ingestor/config.py` must NOT import crewai (EFSP-329 hook + `tests/test_discipline.py`).
- `anthropic_api_key` uses `field(repr=False)` — the secret must never appear in `repr`/`str`.
- Missing/blank `ANTHROPIC_API_KEY` raises `MissingSettingError`.
- `uv` at `~/.local/bin`; `export PATH="$HOME/.local/bin:$PATH"`; tools via `uv run`.
- Base branch `dev`; feature branch `development/efsp-331-define-settings-dataclass-and-env-example`; PR → `dev`.
- No real secret committed; test env driven by `monkeypatch` with `load_dotenv_file=False`.

## File Structure

- `src/mail_ingestor/config.py` — Modify (stub → implementation).
- `tests/test_config.py` — Create.
- `.env.example` — Modify (add `LLM_MODEL`).

---

### Task 1: Settings dataclass + env loader + `.env.example`

TDD the settings object and its loader, then document the env vars.

**Files:**
- Modify: `src/mail_ingestor/config.py`
- Create: `tests/test_config.py`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `python-dotenv` (already a dep).
- Produces:
  - `mail_ingestor.config.DEFAULT_LLM_MODEL: str` (= `"claude-sonnet-5"`)
  - `mail_ingestor.config.MissingSettingError(RuntimeError)`
  - `mail_ingestor.config.Settings(anthropic_api_key, llm_model=DEFAULT_LLM_MODEL)` — frozen dataclass, `anthropic_api_key` is `repr=False`
  - `Settings.from_env(*, load_dotenv_file: bool = True) -> Settings`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config.py`:

```python
from dataclasses import FrozenInstanceError

import pytest

from mail_ingestor.config import DEFAULT_LLM_MODEL, MissingSettingError, Settings


def test_default_llm_model_constant():
    assert DEFAULT_LLM_MODEL == "claude-sonnet-5"


def test_from_env_uses_default_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    settings = Settings.from_env(load_dotenv_file=False)
    assert settings.anthropic_api_key == "sk-test-123"
    assert settings.llm_model == "claude-sonnet-5"


def test_from_env_honors_llm_model_override(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-8")
    settings = Settings.from_env(load_dotenv_file=False)
    assert settings.llm_model == "claude-opus-4-8"


def test_from_env_missing_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MissingSettingError, match="ANTHROPIC_API_KEY"):
        Settings.from_env(load_dotenv_file=False)


def test_from_env_blank_key_raises(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    with pytest.raises(MissingSettingError):
        Settings.from_env(load_dotenv_file=False)


def test_settings_is_frozen():
    settings = Settings(anthropic_api_key="sk-x")
    with pytest.raises(FrozenInstanceError):
        settings.llm_model = "other"


def test_repr_masks_api_key():
    settings = Settings(anthropic_api_key="sk-super-secret")
    assert "sk-super-secret" not in repr(settings)
    assert "sk-super-secret" not in str(settings)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `export PATH="$HOME/.local/bin:$PATH"; uv run pytest tests/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'Settings' from 'mail_ingestor.config'` (still a stub).

- [ ] **Step 3: Implement `config.py`**

Overwrite `src/mail_ingestor/config.py`:

```python
"""Application settings loaded from environment variables.

Framework-agnostic core — a plain stdlib dataclass with no framework coupling.
Only the LLM settings are defined here; Gmail and persistence settings land with
the tasks that consume them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

DEFAULT_LLM_MODEL = "claude-sonnet-5"


class MissingSettingError(RuntimeError):
    """Raised when a required environment variable is unset or blank."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable application settings.

    ``anthropic_api_key`` is excluded from ``repr`` so the secret never leaks into
    logs or tracebacks.
    """

    anthropic_api_key: str = field(repr=False)
    llm_model: str = DEFAULT_LLM_MODEL

    @classmethod
    def from_env(cls, *, load_dotenv_file: bool = True) -> Settings:
        """Build ``Settings`` from environment variables.

        Loads a local ``.env`` first (unless ``load_dotenv_file`` is False), then reads
        ``ANTHROPIC_API_KEY`` (required) and ``LLM_MODEL`` (optional, defaults to
        ``DEFAULT_LLM_MODEL``).
        """
        if load_dotenv_file:
            load_dotenv()
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise MissingSettingError(
                "ANTHROPIC_API_KEY is required but is unset or blank (see .env.example)."
            )
        llm_model = os.environ.get("LLM_MODEL", "").strip() or DEFAULT_LLM_MODEL
        return cls(anthropic_api_key=api_key, llm_model=llm_model)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: all 7 tests PASS, output pristine (no warnings).

- [ ] **Step 5: Expand `.env.example`**

Overwrite `.env.example`:

```dotenv
# Anthropic (PoC LLM call path)
ANTHROPIC_API_KEY=
LLM_MODEL=claude-sonnet-5

# Gmail OAuth (installed-app flow). Paths to gitignored credential files.
GMAIL_CREDENTIALS_PATH=credentials.json
GMAIL_TOKEN_PATH=token.json
```

- [ ] **Step 6: Lint/format, full suite, discipline check**

Run:
```bash
uv run ruff check src tests
uv run ruff format src tests
uv run pytest -q
grep -rnE '^\s*(from|import)\s+crewai' src/mail_ingestor ; echo "exit=$?"
```
Expected: ruff clean; full suite green (existing tests + 7 new); grep prints nothing, `exit=1`.

- [ ] **Step 7: Commit**

```bash
git add src/mail_ingestor/config.py tests/test_config.py .env.example
git commit -m "feat: add Settings dataclass with env loader + expand .env.example (EFSP-331)"
```

---

## Self-Review

**Spec coverage:**
- `DEFAULT_LLM_MODEL`, `MissingSettingError`, `Settings` (frozen, slots), `from_env` → Task 1 Step 3. ✓
- `anthropic_api_key` required + `repr=False` secret masking → Step 3 + `test_repr_masks_api_key`. ✓
- `llm_model` default + `LLM_MODEL` override → Step 3 + `test_from_env_uses_default_model` / `_honors_llm_model_override`. ✓
- Fail fast on missing/blank key → Step 3 + `test_from_env_missing_key_raises` / `_blank_key_raises`. ✓
- `load_dotenv_file` flag for deterministic tests → Step 3 + tests pass `load_dotenv_file=False`. ✓
- `.env.example` documents `ANTHROPIC_API_KEY`, `LLM_MODEL`, Gmail vars → Step 5. ✓
- Discipline (no crewai import) → Step 6 grep. ✓

**Placeholder scan:** No TBD/TODO; all code complete. ✓

**Type/consistency check:** `from_env(*, load_dotenv_file=True) -> Settings` matches the interface and all test call sites; `DEFAULT_LLM_MODEL` referenced consistently (constant, default, and assertion); `field(repr=False)` on the required `anthropic_api_key` keeps it ordered before the defaulted `llm_model` (valid dataclass field order). ✓
