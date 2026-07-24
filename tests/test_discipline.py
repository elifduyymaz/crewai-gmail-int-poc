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
    return [p for p in _PACKAGE_ROOT.rglob("*.py") if _CREW_DIR not in p.parents and p != _CREW_DIR]


def test_no_crewai_import_in_core() -> None:
    offenders = [
        str(p.relative_to(_PACKAGE_ROOT))
        for p in _core_python_files()
        if _CREWAI_IMPORT.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        f"crewai imported in framework-agnostic core (allowed only under crew/): {offenders}"
    )


def test_core_files_are_actually_scanned() -> None:
    # Guards against the invariant silently passing because no files were found.
    scanned = {p.name for p in _core_python_files()}
    assert {"schemas.py", "redaction.py"} <= scanned
