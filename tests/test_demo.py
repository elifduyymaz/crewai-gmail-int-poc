"""Tests for the ``--demo`` offline pipeline (Task 5.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mail_ingestor import demo as demo_mod
from mail_ingestor.demo import (
    DEMO_CREATED_AT,
    DEMO_MODEL,
    DemoLookupError,
    _iter_email_paths,
    iter_demo_messages,
    load_demo_llm_responses,
    run_demo_batch,
)
from mail_ingestor.schemas import RawMessage, SummaryRecord

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "emails"


# ─────────────────────────────────────────────────────────────
# Fixture loader
# ─────────────────────────────────────────────────────────────


def test_iter_email_paths_yields_five_email_fixtures() -> None:
    paths = list(_iter_email_paths(_FIXTURES_DIR))
    assert len(paths) == 5
    assert all(p.suffix == ".json" for p in paths)


def test_iter_email_paths_skips_llm_responses_json() -> None:
    paths = [p.name for p in _iter_email_paths(_FIXTURES_DIR)]
    assert "llm_responses.json" not in paths


def test_iter_email_paths_skips_poison_prefix_files() -> None:
    paths = [p.name for p in _iter_email_paths(_FIXTURES_DIR)]
    assert not any(p.startswith("poison_") for p in paths)


def test_iter_email_paths_returns_sorted_paths() -> None:
    # Determinism prerequisite: repeat calls must yield the same order.
    paths_1 = [p.name for p in _iter_email_paths(_FIXTURES_DIR)]
    paths_2 = [p.name for p in _iter_email_paths(_FIXTURES_DIR)]
    assert paths_1 == paths_2 == sorted(paths_1)


def test_iter_demo_messages_returns_raw_messages_keyed_by_id() -> None:
    messages = list(iter_demo_messages(_FIXTURES_DIR))
    assert len(messages) == 5
    assert all(isinstance(m, RawMessage) for m in messages)
    assert {m.message_id for m in messages} == {
        "demo-msg-001",
        "demo-msg-002",
        "demo-msg-003",
        "demo-msg-004",
        "demo-msg-005",
    }


def test_iter_demo_messages_preserves_payload_shape() -> None:
    first = next(iter_demo_messages(_FIXTURES_DIR))
    assert first.payload["id"] == first.message_id
    # Gmail API resource dict fields that parse_gmail_message needs.
    assert "internalDate" in first.payload
    assert "payload" in first.payload
    assert "headers" in first.payload["payload"]


def test_iter_demo_messages_raises_on_fixture_missing_id(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"payload": {}}', encoding="utf-8")
    with pytest.raises(ValueError, match="missing top-level 'id'"):
        list(iter_demo_messages(tmp_path))


# ─────────────────────────────────────────────────────────────
# LLM responses loader
# ─────────────────────────────────────────────────────────────


def test_load_demo_llm_responses_is_keyed_by_message_id() -> None:
    responses = load_demo_llm_responses(_FIXTURES_DIR / "llm_responses.json")
    assert set(responses) == {
        "demo-msg-001",
        "demo-msg-002",
        "demo-msg-003",
        "demo-msg-004",
        "demo-msg-005",
    }


def test_load_demo_llm_responses_entries_are_summary_shaped() -> None:
    responses = load_demo_llm_responses(_FIXTURES_DIR / "llm_responses.json")
    entry = responses["demo-msg-001"]
    assert set(entry) >= {"tl_dr", "summary", "key_points", "action_items", "category"}


def test_load_demo_llm_responses_rejects_top_level_list(tmp_path: Path) -> None:
    bad = tmp_path / "llm_responses.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(TypeError, match="expected a top-level JSON object"):
        load_demo_llm_responses(bad)


def test_llm_responses_json_stays_under_20kb_ac_ceiling() -> None:
    # Task 5.3 AC #7: llm_responses.json size <= 20 KB. Guard against
    # fixture bloat that would push the demo bundle over the ceiling.
    size = (_FIXTURES_DIR / "llm_responses.json").stat().st_size
    assert size <= 20 * 1024, f"llm_responses.json is {size} bytes (>20 KB ceiling)"


def test_every_email_fixture_has_a_matching_llm_response() -> None:
    responses = load_demo_llm_responses(_FIXTURES_DIR / "llm_responses.json")
    for message in iter_demo_messages(_FIXTURES_DIR):
        assert message.message_id in responses, (
            f"fixture {message.message_id!r} has no canned Summary in "
            f"llm_responses.json — the demo would crash mid-batch."
        )


# ─────────────────────────────────────────────────────────────
# Demo LLM adapter (monkey-patched NativeAnthropicLLM.call)
# ─────────────────────────────────────────────────────────────


def test_demo_llm_call_raises_when_no_context_is_set(monkeypatch) -> None:
    # Use monkeypatch (not bare mutation) so a failing test does not leak
    # module state into the next test under pytest-xdist / pytest-random.
    monkeypatch.setattr(demo_mod, "_DEMO_CURRENT_MESSAGE_ID", None)
    with pytest.raises(RuntimeError, match="no _DEMO_CURRENT_MESSAGE_ID set"):
        demo_mod._demo_llm_call(object(), "prompt")


def test_demo_llm_call_returns_canned_summary_for_known_id(monkeypatch) -> None:
    monkeypatch.setattr(demo_mod, "_DEMO_CURRENT_MESSAGE_ID", "known-1")
    monkeypatch.setattr(
        demo_mod,
        "_DEMO_RESPONSES",
        {
            "known-1": {
                "tl_dr": "T",
                "summary": "S",
                "key_points": [],
                "action_items": [],
                "category": "c",
            }
        },
    )
    result = demo_mod._demo_llm_call(object(), "any prompt")
    payload = json.loads(result)
    assert payload["tl_dr"] == "T"
    assert payload["category"] == "c"


def test_demo_llm_call_raises_lookup_error_for_unknown_id(monkeypatch) -> None:
    monkeypatch.setattr(demo_mod, "_DEMO_CURRENT_MESSAGE_ID", "unknown-x")
    monkeypatch.setattr(demo_mod, "_DEMO_RESPONSES", {"known-1": {}})
    with pytest.raises(DemoLookupError, match="unknown-x"):
        demo_mod._demo_llm_call(object(), "any prompt")


# ─────────────────────────────────────────────────────────────
# End-to-end run_demo_batch
# ─────────────────────────────────────────────────────────────


def test_run_demo_batch_writes_one_json_per_fixture(tmp_path: Path) -> None:
    output_dir = tmp_path / "demo_out"
    rc = run_demo_batch(_FIXTURES_DIR, output_dir)
    assert rc == 0
    written = sorted(output_dir.glob("*.json"))
    assert [p.name for p in written] == [
        "001_sample.json",
        "002_sample.json",
        "003_sample.json",
        "004_sample.json",
        "005_sample.json",
    ]


def test_run_demo_batch_output_validates_as_summary_record(tmp_path: Path) -> None:
    output_dir = tmp_path / "demo_out"
    run_demo_batch(_FIXTURES_DIR, output_dir)
    first = output_dir / "001_sample.json"
    record = SummaryRecord.model_validate_json(first.read_text(encoding="utf-8"))
    assert record.source_message_id == "demo-msg-001"
    assert record.model == DEMO_MODEL


def test_run_demo_batch_freezes_created_at_to_demo_timestamp(tmp_path: Path) -> None:
    output_dir = tmp_path / "demo_out"
    run_demo_batch(_FIXTURES_DIR, output_dir)
    for sample in output_dir.glob("*_sample.json"):
        record = SummaryRecord.model_validate_json(sample.read_text(encoding="utf-8"))
        assert record.created_at == DEMO_CREATED_AT


def test_run_demo_batch_leaves_tokens_none_deterministically(tmp_path: Path) -> None:
    # The mocked NativeAnthropicLLM.call bypasses _record_usage entirely,
    # so token accumulators stay at None. Pin that determinism.
    output_dir = tmp_path / "demo_out"
    run_demo_batch(_FIXTURES_DIR, output_dir)
    for sample in output_dir.glob("*_sample.json"):
        record = SummaryRecord.model_validate_json(sample.read_text(encoding="utf-8"))
        assert record.tokens_prompt is None
        assert record.tokens_completion is None


def test_run_demo_batch_is_byte_identical_across_repeat_runs(tmp_path: Path) -> None:
    # Task 5.3 AC #6: two back-to-back runs must produce byte-identical files.
    first = tmp_path / "first"
    second = tmp_path / "second"
    run_demo_batch(_FIXTURES_DIR, first)
    run_demo_batch(_FIXTURES_DIR, second)
    first_files = sorted(first.glob("*.json"))
    second_files = sorted(second.glob("*.json"))
    assert [p.name for p in first_files] == [p.name for p in second_files]
    for a, b in zip(first_files, second_files, strict=True):
        assert a.read_bytes() == b.read_bytes(), (
            f"non-deterministic output: {a.name} differed between runs"
        )


def test_run_demo_batch_clears_current_message_id_after_each_iteration(
    tmp_path: Path,
) -> None:
    # Guards the try/finally in run_demo_batch: the module-level context
    # must never leak between iterations, otherwise the mocked LLM could
    # return stale data if a downstream refactor invoked kickoff twice
    # for the same "current" id.
    output_dir = tmp_path / "demo_out"
    run_demo_batch(_FIXTURES_DIR, output_dir)
    assert demo_mod._DEMO_CURRENT_MESSAGE_ID is None


def test_run_demo_batch_creates_output_dir_when_missing(tmp_path: Path) -> None:
    output_dir = tmp_path / "nested" / "does_not_exist" / "demo_out"
    assert not output_dir.exists()
    run_demo_batch(_FIXTURES_DIR, output_dir)
    assert output_dir.is_dir()


def test_run_demo_batch_does_not_require_anthropic_auth_token(
    tmp_path: Path, monkeypatch
) -> None:
    # Task 5.3 AC #1: --demo runs without credentials.
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    output_dir = tmp_path / "demo_out"
    rc = run_demo_batch(_FIXTURES_DIR, output_dir)
    assert rc == 0


# ─────────────────────────────────────────────────────────────
# Adversarial review follow-ups (Task 5.3 review agents)
# ─────────────────────────────────────────────────────────────


def test_iter_demo_messages_error_carries_fixture_path_on_malformed_json(
    tmp_path: Path,
) -> None:
    bad = tmp_path / "001_bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match=r"001_bad\.json: invalid JSON"):
        list(iter_demo_messages(tmp_path))


def test_load_demo_llm_responses_error_carries_path_on_malformed_json(
    tmp_path: Path,
) -> None:
    bad = tmp_path / "llm_responses.json"
    bad.write_text("{oops", encoding="utf-8")
    with pytest.raises(ValueError, match=r"llm_responses\.json: invalid JSON"):
        load_demo_llm_responses(bad)


def test_run_demo_batch_rejects_missing_fixtures_dir(tmp_path: Path) -> None:
    # Startup guard: don't quietly produce an empty batch when the caller
    # points at nothing. Wheel-install / cwd-drift scenario.
    missing = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError, match="demo fixtures directory not found"):
        run_demo_batch(missing, tmp_path / "out")


def test_run_demo_batch_rejects_empty_fixtures_dir(tmp_path: Path) -> None:
    # A directory with only llm_responses.json (no emails) or only
    # poison_ fixtures would silently exit 0 — surface it loudly.
    fixtures = tmp_path / "empty"
    fixtures.mkdir()
    (fixtures / "llm_responses.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="no non-poison email fixtures found"):
        run_demo_batch(fixtures, tmp_path / "out")


def test_run_demo_batch_rejects_fixture_missing_from_llm_responses(
    tmp_path: Path,
) -> None:
    # Fixture-drift pre-flight: if a caller adds an email fixture but
    # forgets to update llm_responses.json, catch it BEFORE any I/O so no
    # partial demo/00N_sample.json files land on disk.
    fixtures = tmp_path / "drift"
    fixtures.mkdir()
    (fixtures / "001_only.json").write_text(
        '{"id": "orphan-msg-1", "payload": {"headers": []}, "internalDate": "1700000000000"}',
        encoding="utf-8",
    )
    (fixtures / "llm_responses.json").write_text("{}", encoding="utf-8")

    with pytest.raises(DemoLookupError, match=r"missing canned Summary.*orphan-msg-1"):
        run_demo_batch(fixtures, tmp_path / "out")

    # Pre-flight fired BEFORE any I/O — nothing wrote to the output dir.
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").glob("*.json"))


def test_run_demo_batch_prunes_stale_output_samples(tmp_path: Path) -> None:
    # A previous run left a 006_sample.json behind; the current fixture
    # set only has 5 entries. The stale file must be gone after this run.
    output_dir = tmp_path / "demo_out"
    output_dir.mkdir()
    stale = output_dir / "006_sample.json"
    stale.write_text('{"stale": true}', encoding="utf-8")
    assert stale.exists()

    run_demo_batch(_FIXTURES_DIR, output_dir)

    assert not stale.exists()
    # And only the 5 fresh samples are present.
    assert sorted(p.name for p in output_dir.glob("*_sample.json")) == [
        "001_sample.json",
        "002_sample.json",
        "003_sample.json",
        "004_sample.json",
        "005_sample.json",
    ]


def test_run_demo_batch_clears_demo_responses_global_after_completion(
    tmp_path: Path,
) -> None:
    # Silent-failure guard: after a successful run, the module-level
    # responses map must be empty. Otherwise a subsequent call with a
    # subset fixture set could ghost-lookup the previous run's data.
    output_dir = tmp_path / "demo_out"
    run_demo_batch(_FIXTURES_DIR, output_dir)
    assert demo_mod._DEMO_RESPONSES == {}


def test_run_demo_batch_clears_demo_responses_even_when_flow_raises(
    tmp_path: Path, monkeypatch
) -> None:
    # If flow.kickoff explodes mid-batch (e.g. Pydantic schema drift),
    # the try/finally must still reset the global — otherwise the next
    # in-process caller inherits stale data.
    from mail_ingestor import flow as flow_mod

    def _boom(self, inputs):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated flow failure")

    monkeypatch.setattr(flow_mod.MailIngestorFlow, "kickoff", _boom)
    output_dir = tmp_path / "demo_out"
    with pytest.raises(RuntimeError, match="simulated flow failure"):
        run_demo_batch(_FIXTURES_DIR, output_dir)
    assert demo_mod._DEMO_RESPONSES == {}
    assert demo_mod._DEMO_CURRENT_MESSAGE_ID is None


def test_demo_reader_key_error_includes_demo_context() -> None:
    # If a caller injects a stale message_id, the KeyError must say
    # "demo reader" so the traceback doesn't look like a Gmail bug.
    reader = demo_mod._DemoGmailReader({"real-1": {"payload": {}}})
    with pytest.raises(KeyError, match=r"demo reader has no fixture.*'stale-999'"):
        reader.get_message("stale-999")


def test_committed_demo_samples_match_a_fresh_run(tmp_path: Path) -> None:
    # AC #6 elevated: the artifact contributors merge (repo-committed
    # demo/*.json) must equal what --demo would produce right now.
    # Schema drift, model rename, or a llm_responses.json edit that
    # someone forgot to regenerate against would leave the committed
    # samples stale — this test fails until the artifacts are refreshed.
    fresh_dir = tmp_path / "fresh"
    run_demo_batch(_FIXTURES_DIR, fresh_dir)
    committed_dir = _FIXTURES_DIR.parent.parent.parent / "demo"
    for fresh in sorted(fresh_dir.glob("*_sample.json")):
        committed = committed_dir / fresh.name
        assert committed.exists(), (
            f"committed sample missing: {committed.relative_to(_FIXTURES_DIR.parent.parent.parent)}"
        )
        assert fresh.read_bytes() == committed.read_bytes(), (
            f"committed {committed.name} is stale; regenerate via "
            f"`python -m mail_ingestor.main --demo` and commit the result."
        )


def test_run_demo_batch_preserves_all_summary_fields_from_canned_response(
    tmp_path: Path,
) -> None:
    # AC #4 depth: not just source_message_id + model, but the full
    # Summary payload (tl_dr, summary, key_points, action_items,
    # category) must round-trip from llm_responses.json → mocked LLM
    # → CrewAI parse → SummaryRecord.summary. A regression where the
    # flow swallows the LLM output and emits an empty summary would
    # previously pass the shape-only test.
    output_dir = tmp_path / "out"
    run_demo_batch(_FIXTURES_DIR, output_dir)

    responses = load_demo_llm_responses(_FIXTURES_DIR / "llm_responses.json")
    for sample_path in sorted(output_dir.glob("*_sample.json")):
        record = SummaryRecord.model_validate_json(sample_path.read_text(encoding="utf-8"))
        expected = responses[record.source_message_id]
        assert record.summary.tl_dr == expected["tl_dr"]
        assert record.summary.summary == expected["summary"]
        assert record.summary.key_points == expected["key_points"]
        assert record.summary.action_items == expected["action_items"]
        assert record.summary.category == expected["category"]


def test_run_demo_batch_restores_native_anthropic_llm_call_after_completion(
    tmp_path: Path,
) -> None:
    # patch.object's __exit__ must return NativeAnthropicLLM.call to the
    # original implementation. A refactor that swaps to a raw setattr
    # without a finally would leave the mock leaked into subsequent
    # non-demo runs (tests + real CLI) — a silent-failure surface that
    # would only surface as strange behavior far from the demo path.
    from mail_ingestor.flow import NativeAnthropicLLM

    original = NativeAnthropicLLM.call
    run_demo_batch(_FIXTURES_DIR, tmp_path / "out")
    assert NativeAnthropicLLM.call is original


def test_demo_llm_call_json_dumps_non_dict_canned_response(monkeypatch) -> None:
    # Boundary: json.dumps accepts any JSON-serializable value, so a
    # rogue list-shaped entry produces "[]" — the failure then surfaces
    # inside CrewAI's Pydantic parse as a clear ValidationError. Verify
    # our mock does not add its own type check that would obscure the
    # real failure site.
    monkeypatch.setattr(demo_mod, "_DEMO_CURRENT_MESSAGE_ID", "any-id")
    monkeypatch.setattr(demo_mod, "_DEMO_RESPONSES", {"any-id": []})
    assert demo_mod._demo_llm_call(object(), "prompt") == "[]"


def test_demo_gmail_reader_satisfies_message_reader_protocol() -> None:
    # Structural typing pin: _DemoGmailReader must remain a valid
    # MessageReader so mypy accepts it without a type: ignore. A
    # refactor that renames or removes get_message would trip this
    # runtime check.
    from mail_ingestor.gmail.reader import MessageReader

    reader = demo_mod._DemoGmailReader({"m1": {"payload": {}}})
    assert isinstance(reader, MessageReader)
