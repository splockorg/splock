"""C.5 — Completion summary written LAST + atomically (path-1, driver-side).

Per orchestrator §4a.4: both completion-summary emit paths write atomically
(write-to-temp + rename) AND the summary write is the LAST action of
each emit path. If a downstream gesture fails after the summary write,
the summary still reflects the run's terminal state.

This test exercises path-1 (driver-side `emit_chain_summary`).
"""

from __future__ import annotations

import pytest
from pathlib import Path


pytestmark = pytest.mark.acceptance


def test_completion_summary_atomic_write_no_tmp_residue(tmp_slug_dir):
    """C.5: emit_chain_summary writes atomically; no .tmp residue survives."""
    from bin._chain_overnight import completion_summary

    payload = completion_summary.CompletionSummaryInput(
        slug="_acceptance_c5",
        chain_id="chain_2026-05-22T12:00:00Z_c5000000",
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

    # Emit the summary.
    summary_path = completion_summary.emit_chain_summary(
        plan_dir=tmp_slug_dir,
        payload=payload,
    )
    assert summary_path.exists(), "Completion summary not written"
    assert summary_path.is_file(), "Completion summary is not a regular file"

    # Atomic-write discipline: no .tmp / dotfile residue.
    residue = [
        p for p in tmp_slug_dir.iterdir()
        if p.suffix == ".tmp" or p.name.startswith(".tmp") or
        (p.name.startswith(".") and "completion" in p.name.lower())
    ]
    assert not residue, f"Atomic-write residue survives: {residue}"

    # File is non-empty and reflects the payload (chain_id appears).
    content = summary_path.read_text(encoding="utf-8")
    assert content.strip(), "Completion summary file is empty"
    assert "c5000000" in content or "_acceptance_c5" in content or \
           "success" in content.lower(), (
        "Completion summary content doesn't reflect payload"
    )
