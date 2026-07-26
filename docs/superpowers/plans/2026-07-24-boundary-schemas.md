# Boundary Pydantic Schemas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Define the pipeline's boundary data contracts as strict Pydantic v2 models in `src/mail_ingestor/schemas.py`, driven by tests.

**Architecture:** One cohesive module holds all boundary DTOs on a shared strict base (`frozen=True`, `extra="forbid"`, `protected_namespaces=()`). Success path: `EmailMessage` (in) → `Summary` (LLM out) → `SummaryRecord` (persisted). Failure path: `DeadLetterRecord` with a `ProcessingStage` enum. No pipeline logic; framework-agnostic (no crewai).

**Tech Stack:** Python 3.11, Pydantic v2, pytest.

## Global Constraints

- All models in `src/mail_ingestor/schemas.py`; tests in `tests/test_schemas.py`.
- `src/mail_ingestor/schemas.py` must NOT import crewai (EFSP-329 discipline hook + `tests/test_discipline.py` enforce this).
- Every model subclasses `_StrictModel` (`frozen=True`, `extra="forbid"`, `protected_namespaces=()`).
- Timestamps use `pydantic.AwareDatetime` (naive datetimes rejected).
- `uv` at `~/.local/bin`; `export PATH="$HOME/.local/bin:$PATH"`; run tools via `uv run`.
- Base branch `dev`; feature branch `development/efsp-330-define-boundary-pydantic-schemas`; PR → `dev`.
- Test output must be pristine (no warnings) — `protected_namespaces=()` exists specifically so the `SummaryRecord.model` field raises no pydantic protected-namespace warning.

## File Structure

- `src/mail_ingestor/schemas.py` — Modify (currently a one-line stub). All boundary models.
- `tests/test_schemas.py` — Create in Task 1; append in Task 2.

---

### Task 1: Success-path schemas (`EmailMessage`, `Summary`, `SummaryRecord`)

TDD the inbound, LLM-output, and persistence boundary models plus the shared strict base.

**Files:**
- Modify: `src/mail_ingestor/schemas.py`
- Create: `tests/test_schemas.py`

**Interfaces:**
- Consumes: nothing (pure schemas).
- Produces:
  - `mail_ingestor.schemas._StrictModel` (base; `frozen`, `extra="forbid"`, `protected_namespaces=()`)
  - `EmailMessage(message_id, sender, received_at, *, thread_id=None, subject="", recipients=[], labels=[], body_text="", snippet=None, attachment_filenames=[])`
  - `Summary(tl_dr, summary, category, *, key_points=[], action_items=[])`
  - `SummaryRecord(source_message_id, summary: Summary, model, *, subject="", created_at=<utcnow>)`
  - `_utcnow() -> datetime` (aware UTC helper)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_schemas.py`:

```python
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from mail_ingestor.schemas import EmailMessage, Summary, SummaryRecord

AWARE = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)


def _email(**overrides):
    base = {"message_id": "m1", "sender": "a@example.com", "received_at": AWARE}
    base.update(overrides)
    return EmailMessage(**base)


def _summary(**overrides):
    base = {"tl_dr": "t", "summary": "s", "category": "report"}
    base.update(overrides)
    return Summary(**base)


# --- EmailMessage + base behaviors ---

def test_email_message_minimal_valid():
    msg = _email()
    assert msg.message_id == "m1"
    assert msg.subject == ""
    assert msg.recipients == []
    assert msg.body_text == ""


def test_email_message_full_valid():
    msg = _email(
        thread_id="t1",
        subject="Hi",
        recipients=["b@example.com"],
        labels=["poc/reports"],
        body_text="hello",
        snippet="he...",
        attachment_filenames=["a.pdf"],
    )
    assert msg.labels == ["poc/reports"]
    assert msg.attachment_filenames == ["a.pdf"]


def test_email_message_requires_message_id_nonempty():
    with pytest.raises(ValidationError):
        _email(message_id="")


def test_email_message_requires_sender():
    with pytest.raises(ValidationError):
        EmailMessage(message_id="m1", received_at=AWARE)


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        _email(unexpected="x")


def test_model_is_frozen():
    msg = _email()
    with pytest.raises((ValidationError, TypeError)):
        msg.subject = "changed"


def test_naive_datetime_rejected():
    with pytest.raises(ValidationError):
        _email(received_at=datetime(2026, 7, 24, 12, 0))


def test_email_message_json_round_trip():
    msg = _email(subject="Hi", body_text="hello")
    assert EmailMessage.model_validate_json(msg.model_dump_json()) == msg


# --- Summary ---

def test_summary_valid_and_defaults():
    s = _summary()
    assert s.key_points == []
    assert s.action_items == []


def test_summary_requires_nonempty_text_fields():
    with pytest.raises(ValidationError):
        _summary(tl_dr="")
    with pytest.raises(ValidationError):
        _summary(summary="")
    with pytest.raises(ValidationError):
        _summary(category="")


# --- SummaryRecord ---

def test_summary_record_nests_summary_and_defaults_created_at():
    rec = SummaryRecord(
        source_message_id="m1", subject="Hi", summary=_summary(), model="claude-sonnet"
    )
    assert isinstance(rec.summary, Summary)
    assert rec.created_at.tzinfo is not None


def test_summary_record_accepts_dict_for_nested_summary():
    rec = SummaryRecord(
        source_message_id="m1",
        summary={"tl_dr": "t", "summary": "s", "category": "c"},
        model="m",
    )
    assert rec.summary.tl_dr == "t"


def test_summary_record_requires_nonempty_fields():
    with pytest.raises(ValidationError):
        SummaryRecord(source_message_id="", summary=_summary(), model="m")
    with pytest.raises(ValidationError):
        SummaryRecord(source_message_id="m1", summary=_summary(), model="")


def test_summary_record_json_round_trip():
    rec = SummaryRecord(source_message_id="m1", summary=_summary(), model="m")
    assert SummaryRecord.model_validate_json(rec.model_dump_json()) == rec


def test_summary_record_no_protected_namespace_warning(recwarn):
    SummaryRecord(source_message_id="m1", summary=_summary(), model="m")
    assert not [w for w in recwarn.list if "protected namespace" in str(w.message).lower()]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `export PATH="$HOME/.local/bin:$PATH"; uv run pytest tests/test_schemas.py -v`
Expected: FAIL — `ImportError: cannot import name 'EmailMessage' from 'mail_ingestor.schemas'` (the module is still the stub).

- [ ] **Step 3: Implement the schemas**

Overwrite `src/mail_ingestor/schemas.py`:

```python
"""Boundary data contracts for the ingestion pipeline.

Framework-agnostic core — must not depend on crewai. These Pydantic v2 models
define the edges of the pipeline: the inbound parsed email (``EmailMessage``),
the LLM output (``Summary``), and the persisted record (``SummaryRecord``).
The failure boundary (``DeadLetterRecord``) is defined alongside them.

``frozen=True`` protects field rebinding; the contents of list-typed fields
remain technically mutable, which is acceptable for this PoC.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    """Timezone-aware UTC now (used as a default_factory)."""
    return datetime.now(timezone.utc)


class _StrictModel(BaseModel):
    """Immutable base: forbid unknown fields and mutation after construction.

    ``protected_namespaces=()`` disables Pydantic's ``model_*`` guard so the
    domain field ``SummaryRecord.model`` (the LLM model id) raises no warning.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())


class EmailMessage(_StrictModel):
    """Inbound boundary: a parsed Gmail message handed to the pipeline."""

    message_id: str = Field(min_length=1)
    thread_id: str | None = None
    subject: str = ""
    sender: str
    recipients: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    received_at: AwareDatetime
    body_text: str = ""
    snippet: str | None = None
    attachment_filenames: list[str] = Field(default_factory=list)


class Summary(_StrictModel):
    """LLM output contract (used as ``output_pydantic``)."""

    tl_dr: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    key_points: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    category: str = Field(min_length=1)


class SummaryRecord(_StrictModel):
    """Outbound / persistence boundary: a ``Summary`` plus provenance."""

    source_message_id: str = Field(min_length=1)
    subject: str = ""
    summary: Summary
    model: str = Field(min_length=1)
    created_at: AwareDatetime = Field(default_factory=_utcnow)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_schemas.py -v`
Expected: all tests in the file PASS, output pristine (no warnings). If a protected-namespace warning appears, confirm `protected_namespaces=()` is on `_StrictModel`.

- [ ] **Step 5: Lint/format and confirm discipline intact**

Run:
```bash
uv run ruff check src tests
uv run ruff format src tests
grep -rn "crewai" src/mail_ingestor/schemas.py ; echo "exit=$?"
```
Expected: ruff clean; grep prints nothing, `exit=1`.

- [ ] **Step 6: Commit**

```bash
git add src/mail_ingestor/schemas.py tests/test_schemas.py
git commit -m "feat: add success-path boundary schemas (EmailMessage, Summary, SummaryRecord) (EFSP-330)"
```

---

### Task 2: Failure-path schema (`ProcessingStage`, `DeadLetterRecord`)

TDD the failure boundary and its stage enum.

**Files:**
- Modify: `src/mail_ingestor/schemas.py` (append)
- Modify: `tests/test_schemas.py` (append)

**Interfaces:**
- Consumes: `_StrictModel` from Task 1.
- Produces:
  - `ProcessingStage(str, Enum)` with members `READ="read"`, `PARSE="parse"`, `SUMMARIZE="summarize"`, `PERSIST="persist"`
  - `DeadLetterRecord(stage: ProcessingStage, error, *, source_message_id=None, failed_at=<utcnow>)`

- [ ] **Step 1: Write the failing tests (append)**

Append to `tests/test_schemas.py`. First extend the existing import line
`from mail_ingestor.schemas import EmailMessage, Summary, SummaryRecord` to also import
`DeadLetterRecord` and `ProcessingStage`:

```python
from mail_ingestor.schemas import (
    DeadLetterRecord,
    EmailMessage,
    ProcessingStage,
    Summary,
    SummaryRecord,
)
```

Then append these tests at the end of the file:

```python
# --- ProcessingStage + DeadLetterRecord ---

def test_processing_stage_values():
    assert {s.value for s in ProcessingStage} == {"read", "parse", "summarize", "persist"}


def test_dead_letter_valid_with_enum():
    d = DeadLetterRecord(stage=ProcessingStage.PARSE, error="boom")
    assert d.stage is ProcessingStage.PARSE
    assert d.source_message_id is None
    assert d.failed_at.tzinfo is not None


def test_dead_letter_accepts_stage_string():
    d = DeadLetterRecord(stage="summarize", error="x")
    assert d.stage is ProcessingStage.SUMMARIZE


def test_dead_letter_rejects_unknown_stage():
    with pytest.raises(ValidationError):
        DeadLetterRecord(stage="unknown", error="x")


def test_dead_letter_requires_nonempty_error():
    with pytest.raises(ValidationError):
        DeadLetterRecord(stage=ProcessingStage.READ, error="")


def test_dead_letter_json_round_trip():
    d = DeadLetterRecord(
        source_message_id="m1", stage=ProcessingStage.PERSIST, error="db down"
    )
    assert DeadLetterRecord.model_validate_json(d.model_dump_json()) == d
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_schemas.py -k "dead_letter or processing_stage" -v`
Expected: FAIL — `ImportError` for `DeadLetterRecord` / `ProcessingStage`.

- [ ] **Step 3: Implement the failure schema (append)**

Add `from enum import Enum` to the imports in `src/mail_ingestor/schemas.py` (with the other stdlib imports), then append these classes at the end of the module:

```python
class ProcessingStage(str, Enum):
    """Pipeline stage where processing of a message can fail."""

    READ = "read"
    PARSE = "parse"
    SUMMARIZE = "summarize"
    PERSIST = "persist"


class DeadLetterRecord(_StrictModel):
    """Failure boundary: a message that could not be processed."""

    source_message_id: str | None = None
    stage: ProcessingStage
    error: str = Field(min_length=1)
    failed_at: AwareDatetime = Field(default_factory=_utcnow)
```

- [ ] **Step 4: Run the full schema test suite to verify it passes**

Run: `uv run pytest tests/test_schemas.py -v`
Expected: every test passes, output pristine.

- [ ] **Step 5: Lint/format and confirm discipline intact**

Run:
```bash
uv run ruff check src tests
uv run ruff format src tests
uv run pytest -q
grep -rn "crewai" src/mail_ingestor/schemas.py ; echo "exit=$?"
```
Expected: ruff clean; full suite green; grep prints nothing, `exit=1`.

- [ ] **Step 6: Commit**

```bash
git add src/mail_ingestor/schemas.py tests/test_schemas.py
git commit -m "feat: add failure-path boundary schema (DeadLetterRecord, ProcessingStage) (EFSP-330)"
```

---

## Self-Review

**Spec coverage:**
- `_StrictModel` base (frozen, extra forbid, protected_namespaces) → Task 1 Step 3. ✓
- `EmailMessage`, `Summary`, `SummaryRecord` (nested) → Task 1. ✓
- `ProcessingStage`, `DeadLetterRecord` → Task 2. ✓
- AwareDatetime timestamps + default_factory utcnow → Tasks 1 & 2. ✓
- Tests: valid construction, extra forbid, frozen, missing-required, naive-datetime, JSON round-trip, nesting, enum accept/reject → Tasks 1 & 2. ✓
- No protected-namespace warning on `model` field → Task 1 `test_summary_record_no_protected_namespace_warning` + `protected_namespaces=()`. ✓
- Discipline (no crewai in schemas.py) → grep check in both tasks' Step 5. ✓

**Placeholder scan:** No TBD/TODO; all code is complete. ✓

**Type/consistency check:** `_summary()`/`_email()` helpers used consistently; import line extended in Task 2 matches the models added; `SummaryRecord.summary: Summary` nesting matches the nesting test; `ProcessingStage` members match the enum test set; `_utcnow` referenced as default_factory in both `SummaryRecord.created_at` and `DeadLetterRecord.failed_at`. ✓
