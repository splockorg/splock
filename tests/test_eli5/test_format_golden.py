"""Byte-stability guard for all three assembled eli5 formats.

Per the rubric-determinism doctrine (`bin/_qa/rubric.py`, arXiv:2506.22316 /
2509.26072): the eli5 output format MUST be deterministically constructed and
byte-stable, never agent-authored. Mirrors `tests/test_qa/
test_rubric_byte_stability.py`: each mode is pinned against a committed,
human-auditable snapshot fixture (a diff on a failing run shows exactly which
byte moved — a checksum cannot).

If a test goes red, the format drifted. Fix `bin/_eli5/format.py` — or, for an
intentional format change, refresh the matching fixture in the same commit so
the diff is visible in review. Never edit a snapshot to match accidental drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._eli5.format import MODES, build_format  # noqa: E402

_FIXTURES = Path(__file__).parent / "_fixtures"


@pytest.mark.parametrize("mode", MODES)
def test_format_byte_stable_against_snapshot(mode: str) -> None:
    snapshot = _FIXTURES / f"{mode}_format_snapshot_2026_07_18.md"
    assert snapshot.is_file(), f"missing snapshot fixture: {snapshot}"
    assert build_format(mode) == snapshot.read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", MODES)
def test_format_is_pure(mode: str) -> None:
    assert build_format(mode) == build_format(mode)


def test_modes_are_the_closed_enum() -> None:
    assert MODES == ("auto", "decision", "informative")
    with pytest.raises(ValueError):
        build_format("verbose")


def test_every_mode_carries_the_spine_and_its_own_clause() -> None:
    for mode in MODES:
        text = build_format(mode)
        # the five-part block template
        for part in ("**ELI5:**", "**Example:**", "**Impact:**",
                     "**TL;DR:**", "**Options:**"):
            assert part in text, f"{mode}: missing {part}"
        # all eight discipline rules present (numbered list markers)
        for n in range(1, 9):
            assert f"\n{n}. " in text, f"{mode}: missing rule {n}"
        assert f"Mode: {mode}" in text
    # mode clauses genuinely differ
    assert len({build_format(m) for m in MODES}) == 3
