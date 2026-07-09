"""J.10 — Completion summary collision case: dual emit-paths preserve atomicity.

Per inventory:
- Source: implplan §A.impl.7 + Opus M-8 (completion-summary collision case
  not exercised by current tests).
- Expected outcome: if both emit paths fire near-simultaneously, atomic
  write discipline holds — no partial-write artifact survives + each
  emission lands at its own distinct filename (chain_id-stamped per
  §A.5a).

Pass 6: uses the `concurrent_writer_simulator` fixture to fire two
emissions truly concurrently via ThreadPoolExecutor (rather than
sequentially, which can't surface race conditions).
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def _build_payload(chain_id_suffix: str):
    """Build a CompletionSummaryInput with a unique chain_id."""
    from bin._chain_overnight import completion_summary
    return completion_summary.CompletionSummaryInput(
        slug="acceptance-j10",
        chain_id=f"chain_2026-05-22T12:00:00Z_{chain_id_suffix}",
        chain_started_at="2026-05-22T12:00:00Z",
        chain_ended_at="2026-05-22T13:00:00Z",
        halt_reason="success",
        driver_exit_code=0,
        phases=(),
        committed_files=(),
        wall_clock_cap_seconds=43200,
        wall_clock_total_seconds=3600,
        cost_total_usd=2.5,
    )


def test_concurrent_summary_writes_preserve_atomicity(
    tmp_slug_dir, concurrent_writer_simulator
):
    """J.10: two near-simultaneous summary emissions preserve atomicity.

    Both emit-paths in §A.5a share the same filename pattern with
    chain_id_short discriminator. Concurrent emissions must produce
    two distinct files (no collision), neither overwriting the other,
    and leave no .tmp residue from the atomic-rename pattern.
    """
    from bin._chain_overnight import completion_summary

    payload_a = _build_payload("aaa00aaa")
    payload_b = _build_payload("bbb00bbb")

    def _emit(payload):
        return completion_summary.emit_chain_summary(
            plan_dir=tmp_slug_dir,
            payload=payload,
        )

    # Fire both emissions concurrently via the ThreadPoolExecutor fixture.
    results = concurrent_writer_simulator(_emit, [(payload_a,), (payload_b,)],
                                           max_workers=2)
    # Both writes should succeed.
    failures = [r for r in results if isinstance(r, Exception)]
    assert not failures, f"Concurrent emissions raised: {failures}"

    # Two distinct files should be produced.
    summary_files = list(tmp_slug_dir.glob("_completion_summary_*.md"))
    assert len(summary_files) == 2, (
        f"Expected 2 distinct summary files; got {len(summary_files)}: "
        f"{[p.name for p in summary_files]}"
    )

    # Neither file should be empty.
    for sf in summary_files:
        assert sf.read_text(encoding="utf-8").strip(), f"{sf.name} is empty"

    # No .tmp residue from atomic-rename pattern.
    tmp_residue = (
        list(tmp_slug_dir.glob("*.tmp"))
        + list(tmp_slug_dir.glob(".*tmp*"))
        + list(tmp_slug_dir.glob("*~"))
    )
    assert not tmp_residue, (
        f"Atomic-write residue (.tmp file) survives after concurrent writes: "
        f"{[p.name for p in tmp_residue]}"
    )

    # Files should have distinct chain-id components in the name.
    names = sorted(p.name for p in summary_files)
    assert "aaa00aaa" in names[0] or "aaa00aaa" in names[1]
    assert "bbb00bbb" in names[0] or "bbb00bbb" in names[1]
