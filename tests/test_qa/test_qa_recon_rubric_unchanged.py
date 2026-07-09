"""The recon-rubric regression linchpin (qa_review_target_generalization T3).

Per the plan's SC3 + the T3 contract: the rubric refactor splits the
single recon-shaped ``RUBRIC_MD`` into a frozen shared spine + a frozen
per-subject ``RUBRIC_BLOCKS`` table, assembled by a pure
``build_rubric(subject)``. The ONE invariant this refactor must never
break is that the *recon* subject — the only subject that existed before
this slug — keeps producing the exact same rubric byte-for-byte.

This test pins that invariant against a byte-frozen snapshot of today's
``RUBRIC_MD`` (captured before the refactor, committed at
``_fixtures/recon_rubric_snapshot_2026_05_29.md``). A single changed
byte in the spine, a per-kind block, the assembly glue, or the join
separators — whitespace, a moved newline, a reordered block — fails this
test.

If this test goes red, the decomposition's glue is off: fix the
assembler / spine, NEVER the snapshot. The snapshot is the immutable
"what recon's rubric was on 2026-05-29" record; it is the regression
oracle, not a thing to be edited to match a drifted implementation.

(This is distinct from T4's ``test_rubric_byte_stability.py``, which pins
ALL FOUR assembled rubrics. This test exists in T3 to guard the recon
linchpin the moment the refactor lands, before T4 adds the broader pin.)
"""

from __future__ import annotations

from pathlib import Path


from bin._qa.rubric import RUBRIC_MD, build_rubric


# The byte-frozen snapshot of today's RUBRIC_MD, captured verbatim before
# the T3 refactor. Read with newline="" so no platform newline translation
# can mask a real byte difference.
_SNAPSHOT_PATH = (
    Path(__file__).parent / "_fixtures" / "recon_rubric_snapshot_2026_05_29.md"
)


def _read_snapshot() -> str:
    return _SNAPSHOT_PATH.read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# The linchpin
# ----------------------------------------------------------------------

def test_build_rubric_recon_equals_frozen_snapshot() -> None:
    """``build_rubric('recon')`` equals today's ``RUBRIC_MD`` byte-for-byte.

    This is THE regression guard for the only subject that existed before
    the rubric was generalized. If it fails, the spine/blocks
    decomposition or its join glue has drifted from the historical text.
    """
    snapshot = _read_snapshot()
    assembled = build_rubric("recon")
    assert assembled == snapshot, (
        "build_rubric('recon') drifted from the frozen recon-rubric "
        "snapshot. Fix the spine/blocks decomposition or its join glue — "
        "do NOT edit the snapshot; it is the immutable regression oracle."
    )


def test_recon_assembly_is_byte_exact_length() -> None:
    """A length-level guard that fails loudly on whitespace/newline drift
    even if a future change happens to keep the same characters in a
    different arrangement of the same length (cheap belt-and-suspenders)."""
    snapshot = _read_snapshot()
    assembled = build_rubric("recon")
    assert len(assembled.encode("utf-8")) == len(snapshot.encode("utf-8"))


def test_rubric_md_alias_equals_recon_assembly() -> None:
    """The back-compat ``RUBRIC_MD`` alias is exactly
    ``build_rubric('recon')`` (and therefore the frozen snapshot). This
    pins that the alias retained for the package re-export cannot silently
    diverge from the recon assembly."""
    assert RUBRIC_MD == build_rubric("recon")
    assert RUBRIC_MD == _read_snapshot()


def test_snapshot_fixture_is_present_and_nonempty() -> None:
    """Guard the oracle itself: the committed snapshot fixture must exist
    and be non-empty, so a missing/empty fixture can never make the
    linchpin vacuously pass."""
    assert _SNAPSHOT_PATH.is_file(), (
        f"frozen recon-rubric snapshot missing at {_SNAPSHOT_PATH}; "
        "the linchpin test cannot run without its byte oracle"
    )
    assert _read_snapshot().strip(), "snapshot fixture is empty"
