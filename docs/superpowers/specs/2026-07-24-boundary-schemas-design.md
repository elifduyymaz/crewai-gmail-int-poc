# EFSP-330 — Define Boundary Pydantic Schemas

**Date:** 2026-07-24
**Task:** EFSP-330 "1.3 Define boundary Pydantic schemas"
**Base branch:** `dev` (has EFSP-328 scaffold + EFSP-329 guardrails)
**Feature branch:** `development/efsp-330-define-boundary-pydantic-schemas`
**PR target:** `dev`

## Purpose

Define the data contracts at the edges of the ingestion pipeline as strict Pydantic v2
models, all in `src/mail_ingestor/schemas.py`. These are the framework-agnostic core the
PRD calls out as reusable regardless of the framework decision — so they must NOT import
crewai (enforced by the EFSP-329 discipline hook + test). No pipeline logic here: just the
schemas and their validation, driven by tests.

## Pipeline boundaries

```
Gmail label → [EmailMessage] → summarizer(LLM) → [Summary] → +provenance → [SummaryRecord] → SQLite
                                     │
                                     └── on failure → [DeadLetterRecord]
```

## Decisions (locked)

- **Schema set:** 3 success-path schemas (`EmailMessage`, `Summary`, `SummaryRecord`) +
  1 failure schema (`DeadLetterRecord`).
- **Summary content fields:** `tl_dr`, `summary`, `key_points`, `action_items`, `category`.
- **Strictness:** every model is `frozen=True` + `extra="forbid"`.
- **Timestamps:** `AwareDatetime` (naive datetimes rejected).
- **`Summary` in `SummaryRecord`:** nested composition (not flattened).
- **`EmailMessage` body:** `body_text` only (HTML is reduced to text by the later MIME parser).
- **`Summary.category`:** free `str` (not enum) so valid-but-unexpected LLM labels don't fail
  validation; `DeadLetterRecord.stage` IS an enum (values are ours, not the LLM's).

## Shared base

```python
class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())
```

## Schemas

### `EmailMessage` — inbound boundary (GmailReaderService output)
- `message_id: str` — Gmail id, `min_length=1`
- `thread_id: str | None = None`
- `subject: str = ""`
- `sender: str` — raw From header
- `recipients: list[str] = []`
- `labels: list[str] = []` — all Gmail labels on the message
- `received_at: AwareDatetime`
- `body_text: str = ""` — plain-text body the LLM summarizes
- `snippet: str | None = None`
- `attachment_filenames: list[str] = []` — names only, no content

### `Summary` — LLM output contract (`output_pydantic=Summary`)
- `tl_dr: str` — `min_length=1`
- `summary: str` — `min_length=1`
- `key_points: list[str] = []`
- `action_items: list[str] = []`
- `category: str` — `min_length=1`

### `SummaryRecord` — outbound / persistence boundary
- `source_message_id: str` — links to `EmailMessage.message_id`, `min_length=1`
- `subject: str = ""`
- `summary: Summary` — nested
- `model: str` — `min_length=1`, the LLM model id used
- `created_at: AwareDatetime` — `default_factory` = UTC now

### `ProcessingStage` — enum
`READ = "read"`, `PARSE = "parse"`, `SUMMARIZE = "summarize"`, `PERSIST = "persist"`
(subclass `str, Enum` for clean JSON serialization).

### `DeadLetterRecord` — failure boundary
- `source_message_id: str | None = None`
- `stage: ProcessingStage`
- `error: str` — `min_length=1`
- `failed_at: AwareDatetime` — `default_factory` = UTC now

## Notes

- `default_factory` for `created_at`/`failed_at` uses a small `_utcnow()` helper returning
  `datetime.now(timezone.utc)`. Tests pass explicit timestamps for determinism where needed.
- `frozen=True` protects field rebinding; list-field contents remain technically mutable —
  acceptable for a PoC and idiomatic. Documented so it is a known limitation, not a surprise.

## Definition of Done

- `src/mail_ingestor/schemas.py` defines `_StrictModel`, `EmailMessage`, `Summary`,
  `SummaryRecord`, `ProcessingStage`, `DeadLetterRecord`.
- `tests/test_schemas.py` covers, for the models: valid construction; `extra="forbid"`
  rejection; `frozen` mutation raising; missing-required raising; naive-datetime rejection;
  JSON round-trip; `SummaryRecord` nesting `Summary`; `DeadLetterRecord` stage enum.
- `uv run pytest` green; `uv run ruff check src tests` + `uv run ruff format --check` clean.
- `grep -r "import crewai" src/` still returns nothing (discipline intact).

## Out of Scope

- Gmail reading / MIME parsing (produces `EmailMessage`) — later task.
- LLM summarization (produces `Summary`) — later task.
- SQLite persistence (consumes `SummaryRecord`) — later task.
- DLQ handling logic (produces `DeadLetterRecord`) — later task; this task only defines the schema.
