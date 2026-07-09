"""Purity + closed-enum contract of ``build_rubric`` (qa_review_target_generalization T3).

Per the plan's SC3: the rubric is generalized to four subject kinds while
remaining deterministically constructed and never agent-authored — a
frozen shared spine + a frozen ``RUBRIC_BLOCKS: dict[Subject, str]``
assembled by a *pure* ``build_rubric(subject) -> str``.

These tests pin the assembler's contract:

1. Purity — same subject yields byte-identical output across repeated
   calls, with no observable side effects (the frozen source constants
   are not mutated by assembly).
2. Closed over the enum — an unknown subject raises (mirrors the
   ``ValueError`` discipline of ``subject_artifact_name``), so an
   out-of-enum subject can never silently fabricate a rubric.
3. ``RUBRIC_BLOCKS`` keys are exactly ``ALL_SUBJECTS`` — all four present,
   no extras, no missing — and the table is read-only.

(The byte-exact CONTENT of each assembled rubric is pinned separately:
recon by ``test_qa_recon_rubric_unchanged.py`` here in T3, and all four by
``test_rubric_byte_stability.py`` in T4. This module pins behavior, not
bytes.)
"""

from __future__ import annotations

from types import MappingProxyType

import pytest

from bin._qa.rubric import RUBRIC_BLOCKS, build_rubric
from bin._qa.subject import ALL_SUBJECTS, SUBJECT_CHOICES


# ----------------------------------------------------------------------
# Purity
# ----------------------------------------------------------------------

@pytest.mark.parametrize("subject", sorted(ALL_SUBJECTS))
def test_build_rubric_is_idempotent(subject: str) -> None:
    """Same subject -> byte-identical output across repeated calls.

    `build_rubric` is a pure function of the closed enum over frozen
    constants; calling it N times must produce N identical strings.
    """
    first = build_rubric(subject)
    again = build_rubric(subject)
    third = build_rubric(subject)
    assert first == again == third


@pytest.mark.parametrize("subject", sorted(ALL_SUBJECTS))
def test_build_rubric_returns_nonempty_str(subject: str) -> None:
    """Every valid subject assembles to a non-empty markdown string."""
    result = build_rubric(subject)
    assert isinstance(result, str)
    assert result.strip()
    # The assembled rubric must carry the shared spine scaffolding.
    assert "**Output discipline:**" in result
    assert "**Closing note:**" in result


def test_build_rubric_has_no_observable_side_effects() -> None:
    """Assembly must not mutate the frozen source table.

    Snapshot the per-subject block table before assembling every subject,
    then confirm the table is byte-for-byte unchanged afterward — proving
    `build_rubric` reads but never writes its frozen inputs.
    """
    before = {k: RUBRIC_BLOCKS[k] for k in RUBRIC_BLOCKS}
    for subject in ALL_SUBJECTS:
        build_rubric(subject)
    after = {k: RUBRIC_BLOCKS[k] for k in RUBRIC_BLOCKS}
    assert before == after


def test_distinct_subjects_yield_distinct_rubrics() -> None:
    """The four subjects produce four DISTINCT rubrics (the per-kind
    blocks are not accidentally collapsed to one shared body)."""
    rendered = {s: build_rubric(s) for s in ALL_SUBJECTS}
    assert len(set(rendered.values())) == len(ALL_SUBJECTS)


# ----------------------------------------------------------------------
# Closed over the enum
# ----------------------------------------------------------------------

@pytest.mark.parametrize("bogus", ["implplan", "PLAN", "", "reconn", "qa"])
def test_build_rubric_rejects_unknown_subject(bogus: str) -> None:
    """An unknown subject raises ``ValueError`` rather than silently
    fabricating a rubric (closed-enum discipline, mirroring
    ``subject_artifact_name``)."""
    with pytest.raises(ValueError):
        build_rubric(bogus)


# ----------------------------------------------------------------------
# RUBRIC_BLOCKS table integrity
# ----------------------------------------------------------------------

def test_rubric_blocks_keys_equal_all_subjects() -> None:
    """``RUBRIC_BLOCKS`` covers exactly the closed enum — all four
    subjects present, no extras, no missing ones."""
    assert set(RUBRIC_BLOCKS.keys()) == ALL_SUBJECTS
    assert set(RUBRIC_BLOCKS.keys()) == set(SUBJECT_CHOICES)


def test_rubric_blocks_is_read_only() -> None:
    """``RUBRIC_BLOCKS`` is a read-only mapping (``MappingProxyType``) so
    the per-kind block table cannot be mutated at runtime — the rubric
    stays frozen / non-agent-authored."""
    assert isinstance(RUBRIC_BLOCKS, MappingProxyType)
    with pytest.raises(TypeError):
        RUBRIC_BLOCKS["recon"] = "tampered"  # type: ignore[index]


@pytest.mark.parametrize("subject", sorted(ALL_SUBJECTS))
def test_rubric_blocks_values_are_nonempty_str(subject: str) -> None:
    """Each per-kind block body is a non-empty string carrying H2 block
    headings (the lettered taxonomy the spine wraps)."""
    block = RUBRIC_BLOCKS[subject]
    assert isinstance(block, str)
    assert block.strip()
    assert "## Block A" in block
