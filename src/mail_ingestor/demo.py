"""``--demo`` offline mode: run the ingestion pipeline against committed
fixtures without touching Gmail or an LLM.

**End-to-end contract.** The pipeline stages (fetch → parse → summarize)
run their real code with two substitutions:

1. **Gmail**: an in-memory :class:`_DemoGmailReader` replaces the real
   ``GmailReaderService``. It returns the fixture JSONs verbatim.
2. **LLM**: :meth:`mail_ingestor.flow.NativeAnthropicLLM.call` is
   monkey-patched to return canned ``Summary`` JSONs from
   ``llm_responses.json`` keyed by the currently-processing
   ``message_id`` (tracked by a module-level context variable that the
   batch loop sets before each ``flow.kickoff`` and clears after).

**Determinism.** ``SummaryRecord.created_at`` normally comes from
``schemas._utcnow`` (wall clock). Because Pydantic captures the
``default_factory`` at class-definition time, monkey-patching
``_utcnow`` at runtime is a no-op. Instead, the batch calls
``record.model_copy(update={"created_at": DEMO_CREATED_AT})`` on each
successful kickoff — the resulting model_dump_json output is
byte-identical across repeat runs.

**No credentials needed.** ``--demo`` skips ``_bootstrap`` entirely so
``ANTHROPIC_AUTH_TOKEN`` is not required. ``CREWAI_TELEMETRY_OPT_OUT``
is set on module import so any transitive CrewAI import stays offline.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

# Must land BEFORE any transitive ``import crewai`` — mirrors the
# bootstrap ordering invariant in main._bootstrap.
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "1")

from mail_ingestor.schemas import RawMessage

logger = logging.getLogger(__name__)

DEMO_MODEL = "claude-demo-offline"
DEMO_CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)

_LLM_RESPONSES_FILENAME = "llm_responses.json"
_POISON_PREFIX = "poison_"

# The monkey-patched LLM call reads this to know which canned response to
# return. Set by :func:`run_demo_batch` before each ``flow.kickoff`` and
# cleared after in a ``finally`` — never leaks across iterations.
_DEMO_CURRENT_MESSAGE_ID: str | None = None
_DEMO_RESPONSES: dict[str, dict[str, Any]] = {}


class DemoLookupError(KeyError):
    """Raised when a fixture references a message_id with no canned Summary."""


def _iter_email_paths(fixtures_dir: Path) -> Iterator[Path]:
    """Yield fixture email JSON paths, excluding poison files and llm_responses.

    Sort order is filename-lexicographic so the batch always processes
    ``001_*`` before ``002_*`` — a determinism prerequisite.
    """
    for path in sorted(fixtures_dir.glob("*.json")):
        if path.name == _LLM_RESPONSES_FILENAME:
            continue
        if path.stem.startswith(_POISON_PREFIX):
            continue
        yield path


def iter_demo_messages(fixtures_dir: Path) -> Iterator[RawMessage]:
    """Yield a :class:`RawMessage` per non-poison email JSON in ``fixtures_dir``."""
    for path in _iter_email_paths(fixtures_dir):
        # Wrap json.loads to attach fixture path context — a malformed
        # fixture must not surface as a bare "line 7 column 3" error.
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: invalid JSON: {exc}") from exc
        message_id = payload.get("id")
        if not message_id:
            raise ValueError(f"{path}: fixture missing top-level 'id' field")
        yield RawMessage(message_id=message_id, payload=payload)


def load_demo_llm_responses(path: Path) -> dict[str, dict[str, Any]]:
    """Load ``llm_responses.json`` into a ``{message_id: summary_dict}`` map."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise TypeError(
            f"{path}: expected a top-level JSON object, got {type(parsed).__name__}"
        )
    return parsed


def _demo_llm_call(_self: Any, _messages: Any, **_kw: Any) -> str:
    """Replacement for ``NativeAnthropicLLM.call`` while a demo run is active.

    Ignores the prompt entirely and returns the canned ``Summary`` JSON
    for the currently-processing message. Raises loudly if called
    without an active context — a "leaky" call would otherwise silently
    return stale data from the previous iteration.
    """
    if _DEMO_CURRENT_MESSAGE_ID is None:
        raise RuntimeError(
            "demo LLM adapter called with no _DEMO_CURRENT_MESSAGE_ID set. "
            "The flow invoked the LLM outside run_demo_batch's per-message "
            "context — the demo would produce non-deterministic output."
        )
    try:
        canned = _DEMO_RESPONSES[_DEMO_CURRENT_MESSAGE_ID]
    except KeyError as exc:
        raise DemoLookupError(
            f"No canned Summary for message_id={_DEMO_CURRENT_MESSAGE_ID!r}; "
            f"add it to {_LLM_RESPONSES_FILENAME}."
        ) from exc
    return json.dumps(canned)


class _DemoGmailReader:
    """Minimal duck-typed stand-in for ``GmailReaderService`` in demo mode.

    ``MailIngestorFlow.fetch`` only calls ``reader.get_message`` — a
    lookup in this pre-loaded dict. No API client, no auth, no network.
    """

    def __init__(self, messages: dict[str, dict[str, Any]]) -> None:
        self._messages = messages

    def get_message(self, message_id: str) -> dict[str, Any]:
        try:
            return self._messages[message_id]
        except KeyError as exc:
            # Wrap so a Gmail-style traceback does not hide the fact that
            # we are inside the demo reader with a fixed fixture set.
            raise KeyError(
                f"demo reader has no fixture for message_id={message_id!r}"
            ) from exc


def run_demo_batch(
    fixtures_dir: Path,
    output_dir: Path,
    *,
    model: str = DEMO_MODEL,
) -> int:
    """Run the offline demo pipeline end-to-end.

    Iterates fixtures in ``fixtures_dir``, drives each through the real
    :class:`MailIngestorFlow` with the mocked LLM (see module docstring),
    and writes each :class:`SummaryRecord` as JSON to
    ``output_dir/00N_sample.json``. Returns 0 on success; propagates any
    exception raised inside the flow (demo mode has no DLQ boundary —
    a fixture failure is a bug in the fixture set, not a runtime
    concern to swallow).

    Startup contract (all validated **before** any I/O):

    * The fixtures directory must be a real directory.
    * At least one non-poison email fixture must exist.
    * Every fixture's ``message_id`` must have a matching key in
      ``llm_responses.json`` — a partial responses map would otherwise
      let the batch write ``001..N-1_sample.json`` and only crash on
      the missing entry, leaving stale files behind.

    Startup guarantees (before any ``flow.kickoff`` fires):

    * The output directory is created and any pre-existing
      ``*_sample.json`` files are removed. A shrinking fixture set
      leaves no orphan.

    Cleanup:

    * ``_DEMO_RESPONSES`` is cleared in a ``finally`` — no cross-run
      global-state carryover.
    """
    global _DEMO_CURRENT_MESSAGE_ID, _DEMO_RESPONSES

    if not fixtures_dir.is_dir():
        raise FileNotFoundError(
            f"demo fixtures directory not found: {fixtures_dir}"
        )

    # Single outer try/finally covers every path from "responses map is
    # assigned" through the flow loop. Even if ``iter_demo_messages``
    # raises mid-load (e.g. bad JSON in fixture 3), the finally still
    # clears the module-level state — no partial-assignment leak.
    try:
        _DEMO_RESPONSES = load_demo_llm_responses(fixtures_dir / _LLM_RESPONSES_FILENAME)
        raw_messages = list(iter_demo_messages(fixtures_dir))
        if not raw_messages:
            raise RuntimeError(
                f"{fixtures_dir}: no non-poison email fixtures found — nothing to demo."
            )

        # Pre-flight: every fixture must have a canned Summary. Detecting
        # drift here means partial output cannot be written.
        fixture_ids = {r.message_id for r in raw_messages}
        missing = fixture_ids - _DEMO_RESPONSES.keys()
        if missing:
            raise DemoLookupError(
                f"{_LLM_RESPONSES_FILENAME} missing canned Summary for "
                f"message_ids={sorted(missing)}"
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        # Prune stale samples so a shrinking fixture set never leaves
        # orphaned ``006_sample.json`` behind claiming to be current output.
        for stale in output_dir.glob("*_sample.json"):
            stale.unlink()

        # Deferred imports: everything that transitively touches ``crewai``
        # must load AFTER CREWAI_TELEMETRY_OPT_OUT is set (top of this file).
        from mail_ingestor.flow import MailIngestorFlow, NativeAnthropicLLM

        # ``_DemoGmailReader`` satisfies the structural ``MessageReader``
        # Protocol declared in ``gmail/reader.py`` — mypy accepts it without
        # a subclass relationship and no ``type: ignore`` is needed.
        reader = _DemoGmailReader({r.message_id: r.payload for r in raw_messages})
        flow = MailIngestorFlow(reader=reader, model=model)

        logger.info(
            "demo_starting fixtures_dir=%s output_dir=%s fixture_count=%d",
            fixtures_dir,
            output_dir,
            len(raw_messages),
        )

        with patch.object(NativeAnthropicLLM, "call", _demo_llm_call):
            for index, raw in enumerate(raw_messages, start=1):
                _DEMO_CURRENT_MESSAGE_ID = raw.message_id
                try:
                    record = flow.kickoff(inputs={"message_id": raw.message_id})
                finally:
                    _DEMO_CURRENT_MESSAGE_ID = None
                # Freeze created_at so two back-to-back runs produce
                # byte-identical output. ``model_copy`` respects
                # ``frozen=True`` — it returns a new instance rather than
                # mutating.
                record = record.model_copy(update={"created_at": DEMO_CREATED_AT})
                output_path = output_dir / f"{index:03d}_sample.json"
                output_path.write_text(
                    record.model_dump_json(indent=2) + "\n",
                    encoding="utf-8",
                )
                logger.info(
                    "demo_written path=%s message_id=%s",
                    output_path,
                    record.source_message_id,
                )
    finally:
        # Clear the module-level responses map so a subsequent in-process
        # ``run_demo_batch`` cannot silently reuse the prior run's data
        # (defense in depth — the next call also reassigns from disk).
        _DEMO_RESPONSES = {}

    logger.info("demo_batch_complete count=%d output_dir=%s", len(raw_messages), output_dir)
    return 0
