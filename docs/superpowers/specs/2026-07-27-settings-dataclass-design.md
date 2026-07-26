# EFSP-331 — Define Settings Dataclass and .env.example

**Date:** 2026-07-27
**Task:** EFSP-331 "1.4 Define settings dataclass and .env.example"
**Base branch:** `dev` (has EFSP-328/329/330)
**Feature branch:** `development/efsp-331-define-settings-dataclass-and-env-example`
**PR target:** `dev`

## Purpose

Define the application's configuration as a strict, framework-agnostic settings object
loaded from the environment, and expand `.env.example` to document the variables. Scope is
LLM-only for now (`ANTHROPIC_API_KEY`, `LLM_MODEL`); Gmail/persistence/run-limit settings
are deferred to the tasks that consume them (YAGNI). No live keys are needed — the loader
is tested with fake env values.

## Decisions (locked)

- **Implementation:** stdlib `@dataclass(frozen=True, slots=True)` + a `from_env()`
  classmethod using `python-dotenv`. No new dependency; zero framework coupling (matches
  the ticket's "dataclass" wording and the PRD's framework-agnostic-core ethos).
- **Fields (LLM-only):** `anthropic_api_key: str` (required secret), `llm_model: str`
  (default `claude-sonnet-5`).
- **Missing required var:** fail fast — raise a typed `MissingSettingError` naming the var.

## `src/mail_ingestor/config.py`

```python
DEFAULT_LLM_MODEL = "claude-sonnet-5"

class MissingSettingError(RuntimeError):
    """Raised when a required environment variable is unset or blank."""

@dataclass(frozen=True, slots=True)
class Settings:
    anthropic_api_key: str = field(repr=False)   # secret — never in repr/logs
    llm_model: str = DEFAULT_LLM_MODEL

    @classmethod
    def from_env(cls, *, load_dotenv_file: bool = True) -> "Settings":
        # if load_dotenv_file: load .env (does not override real env)
        # api_key = os.environ["ANTHROPIC_API_KEY"].strip(); raise MissingSettingError if blank
        # model = os.environ.get("LLM_MODEL", "").strip() or DEFAULT_LLM_MODEL
        # return cls(anthropic_api_key=api_key, llm_model=model)
```

Design points:
- **`field(repr=False)` on `anthropic_api_key`** — the secret is excluded from `repr()`/`str()`,
  so it cannot leak into logs or tracebacks. Cheap security win for a fintech repo.
- **`load_dotenv_file` flag** (default `True` for app ergonomics) — tests pass `False` and
  drive `os.environ` via `monkeypatch`, so they never depend on a developer's real `.env`.
- **Fail fast** — blank/unset `ANTHROPIC_API_KEY` raises `MissingSettingError` naming the var;
  `LLM_MODEL` falls back to `DEFAULT_LLM_MODEL`.
- **`frozen=True, slots=True`** — immutable and lightweight.
- Because `anthropic_api_key` has no default and `field(repr=False)` supplies no default,
  it remains a required positional field, correctly ordered before the defaulted `llm_model`.

## `.env.example` (expand)

Add `LLM_MODEL` to the Anthropic section; keep the Gmail entries unchanged:

```dotenv
# Anthropic (PoC LLM call path)
ANTHROPIC_API_KEY=
LLM_MODEL=claude-sonnet-5

# Gmail OAuth (installed-app flow). Paths to gitignored credential files.
GMAIL_CREDENTIALS_PATH=credentials.json
GMAIL_TOKEN_PATH=token.json
```

## Tests (`tests/test_config.py`, TDD, monkeypatch env, `load_dotenv_file=False`)

- `from_env` with `ANTHROPIC_API_KEY` set and no `LLM_MODEL` → api key populated, model =
  `claude-sonnet-5`.
- `LLM_MODEL` set → overrides the default.
- `ANTHROPIC_API_KEY` unset → raises `MissingSettingError` (message names the var).
- `ANTHROPIC_API_KEY` blank/whitespace → raises `MissingSettingError`.
- `Settings` is frozen → mutation raises `FrozenInstanceError`.
- `repr(settings)` / `str(settings)` does NOT contain the api key value (secret masking).
- `DEFAULT_LLM_MODEL == "claude-sonnet-5"`.

## Definition of Done

- `config.py` defines `DEFAULT_LLM_MODEL`, `MissingSettingError`, `Settings`, `Settings.from_env`.
- `.env.example` documents `ANTHROPIC_API_KEY`, `LLM_MODEL`, and the existing Gmail vars.
- `uv run pytest` green; `uv run ruff check src tests` + `ruff format --check` clean.
- `grep -rE '^\s*(from|import)\s+crewai' src/` returns nothing (discipline intact).
- No real secret committed; `.env` stays gitignored.

## Out of Scope

- Gmail credential/token settings, `db_path`, `max_emails`, `log_level` — deferred to their
  consuming tasks.
- Actual LLM invocation or Gmail auth — later tasks.
- `pydantic-settings` — intentionally not added.
