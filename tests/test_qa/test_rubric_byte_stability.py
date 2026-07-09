"""Byte-stability guard for ALL FOUR assembled qa rubrics (qa_review_target_generalization T4).

Per plan §D.8.3 + research_findings_v1.md §D (arXiv:2506.22316 /
2509.26072 rubric-order / score-ID / reference-answer bias): the qa
rubric MUST be deterministically constructed and byte-stable, so it
cannot drift mid-conversation or across releases. T3 refactored the
single recon-shaped rubric into a frozen spine + frozen
``RUBRIC_BLOCKS`` assembled by the pure ``build_rubric(subject)``. This
module is the broad byte-pin the rubric module's docstring promises: it
asserts the byte-exact assembled content of ``build_rubric(s)`` for
EVERY ``s`` in the closed ``ALL_SUBJECTS`` enum.

Each subject is pinned against a committed, human-auditable snapshot
fixture under ``_fixtures/<subject>_rubric_snapshot_2026_05_29.md``. A
committed-fixture pin (rather than a bare checksum) is deliberate: a
diff on a failing run shows the operator exactly which rubric byte
moved — whitespace, a relocated newline, a reordered block — which a
checksum cannot. The recon fixture is the same one T3 captured as its
regression linchpin (``test_qa_recon_rubric_unchanged.py``); reusing it
keeps a single byte oracle for recon across both tests.

If any of these tests goes red, the rubric drifted. Fix the spine /
per-kind block / assembly glue in ``bin/_qa/rubric.py`` — NEVER edit a
snapshot to match a drifted implementation. The snapshots are the
immutable "what each rubric was on 2026-05-29" record; updating the
rubric is an operator-side code edit that intentionally refreshes the
matching fixture in the same commit (so the diff is visible in review).

Distinct from the two T3 tests:
- ``test_qa_recon_rubric_unchanged.py`` pins ONLY recon (the linchpin
  that lands with the refactor itself, before this broad pin exists).
- ``test_qa_build_rubric_purity.py`` pins assembler BEHAVIOR (purity,
  closed-enum rejection, table integrity) — not bytes.
This module pins the byte-exact CONTENT of all four.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bin._qa.rubric import build_rubric
from bin._qa.subject import ALL_SUBJECTS, SUBJECT_CHOICES


# ----------------------------------------------------------------------
# Snapshot oracles.
#
# One committed fixture per subject, captured byte-exact from
# build_rubric(subject) on 2026-05-29. Read with an explicit utf-8
# decode (no platform newline translation) so a real byte difference
# can never be masked.
# ----------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent / "_fixtures"


def _snapshot_path(subject: str) -> Path:
    return _FIXTURE_DIR / f"{subject}_rubric_snapshot_2026_05_29.md"


def _read_snapshot(subject: str) -> str:
    return _snapshot_path(subject).read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# The byte pin — every subject's assembled rubric is byte-exact.
# ----------------------------------------------------------------------

@pytest.mark.parametrize("subject", sorted(ALL_SUBJECTS))
def test_assembled_rubric_is_byte_stable(subject: str) -> None:
    """``build_rubric(subject)`` equals its committed snapshot byte-for-byte.

    The core guard: any change to the spine, a per-kind block, the
    assembly glue, or the join separators — a moved newline, a reordered
    block, a single changed character — fails this test for the affected
    subject. Fix the rubric source, never the snapshot.
    """
    assembled = build_rubric(subject)
    snapshot = _read_snapshot(subject)
    assert assembled == snapshot, (
        f"build_rubric({subject!r}) drifted from its frozen byte oracle at "
        f"{_snapshot_path(subject)}. Fix the spine/blocks/assembly glue in "
        f"bin/_qa/rubric.py — do NOT edit the snapshot to match a drifted "
        f"rubric. If the rubric change is intentional, refresh the snapshot "
        f"in the SAME commit so the byte diff is visible in review."
    )


@pytest.mark.parametrize("subject", sorted(ALL_SUBJECTS))
def test_assembled_rubric_byte_length_matches(subject: str) -> None:
    """A length-level belt-and-suspenders guard.

    Fails loudly on whitespace / newline drift even in the (vanishingly
    unlikely) case where a future change keeps the same characters in a
    different arrangement of the same string but a different UTF-8 byte
    length. Cheap, and it localizes "the bytes moved" vs "a character
    changed" when read alongside the equality assertion above.
    """
    assembled = build_rubric(subject).encode("utf-8")
    snapshot = _read_snapshot(subject).encode("utf-8")
    assert len(assembled) == len(snapshot)


# ----------------------------------------------------------------------
# Guard the oracles themselves.
# ----------------------------------------------------------------------

@pytest.mark.parametrize("subject", sorted(ALL_SUBJECTS))
def test_snapshot_fixture_present_and_nonempty(subject: str) -> None:
    """Each subject's byte oracle must exist and be non-empty.

    A missing or empty fixture would make the byte pin above vacuously
    pass (``read_text`` would raise, but this gives a clearer failure),
    so guard the oracle explicitly.
    """
    path = _snapshot_path(subject)
    assert path.is_file(), (
        f"frozen rubric snapshot missing at {path}; the byte-stability "
        f"pin for subject {subject!r} cannot run without its oracle"
    )
    assert _read_snapshot(subject).strip(), (
        f"snapshot fixture for subject {subject!r} is empty"
    )


def test_every_subject_in_the_closed_enum_is_pinned() -> None:
    """The pinned set is exactly the closed ``ALL_SUBJECTS`` enum.

    This is the structural guard that a FUTURE subject added to
    ``bin/_qa/subject.py`` cannot slip through un-pinned: if someone adds
    a fifth subject without a matching snapshot fixture, this test goes
    red (no fixture file) and the parametrized byte pin also gains a
    case. Equally, a stale fixture for a removed subject is caught by the
    set equality. Pinning is keyed off the live enum, never a hand-kept
    list, so the guard cannot silently fall behind the enum.
    """
    pinned = {
        p.name[: -len("_rubric_snapshot_2026_05_29.md")]
        for p in _FIXTURE_DIR.glob("*_rubric_snapshot_2026_05_29.md")
    }
    assert pinned == ALL_SUBJECTS, (
        "the set of committed rubric snapshot fixtures must equal the "
        f"closed Subject enum. pinned={sorted(pinned)} "
        f"enum={sorted(ALL_SUBJECTS)}. Add (or remove) the matching "
        "snapshot fixture when the Subject enum changes."
    )
    # Cross-check against the deterministically-ordered tuple too.
    assert pinned == set(SUBJECT_CHOICES)
