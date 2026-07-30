"""Framework-agnostic discipline invariants:

  1. ``crewai`` imports live only inside the two fence files defined by
     architecture.md § Provider abstraction: ``flow.py`` and
     ``tools/gmail_tool.py``.
  2. The native LLM SDK (``anthropic``) is imported only in ``llm.py`` — the
     sole provider-abstraction point (NFR-I2).

Mirrors the ``no-crewai-in-core`` pre-commit hook so both invariants are
enforced in CI even when pre-commit is not installed.
"""

from __future__ import annotations

import re
from pathlib import Path

_CREWAI_IMPORT = re.compile(r"^\s*(?:from|import)\s+crewai(?:\b|\.)", re.MULTILINE)
_ANTHROPIC_IMPORT = re.compile(r"^\s*(?:from|import)\s+anthropic(?:\b|\.)", re.MULTILINE)

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "src" / "mail_ingestor"
_LLM_MODULE = _PACKAGE_ROOT / "llm.py"
_FENCE_FILES: frozenset[Path] = frozenset(
    {
        _PACKAGE_ROOT / "flow.py",
        _PACKAGE_ROOT / "tools" / "gmail_tool.py",
    }
)


def _core_python_files() -> list[Path]:
    return [p for p in _PACKAGE_ROOT.rglob("*.py") if p not in _FENCE_FILES]


def test_no_crewai_import_in_core() -> None:
    offenders = [
        str(p.relative_to(_PACKAGE_ROOT))
        for p in _core_python_files()
        if _CREWAI_IMPORT.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        f"crewai imported outside the 2-file fence (crew/ + tools/gmail_tool.py): {offenders}"
    )


def test_core_files_are_actually_scanned() -> None:
    scanned = {p.name for p in _core_python_files()}
    assert {"schemas.py", "redaction.py"} <= scanned


def test_anthropic_import_confined_to_llm_module() -> None:
    offenders = [
        str(p.relative_to(_PACKAGE_ROOT))
        for p in _PACKAGE_ROOT.rglob("*.py")
        if p != _LLM_MODULE and _ANTHROPIC_IMPORT.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "anthropic must be imported only in llm.py — the sole provider "
        f"abstraction point (NFR-I2). Offenders: {offenders}"
    )
