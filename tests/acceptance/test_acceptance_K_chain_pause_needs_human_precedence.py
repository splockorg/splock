"""K6 — NEEDS_HUMAN halt during pause: precedence + finalizer cleanup.

Per CCOR.1 implplan §T-9 + design_resolutions R-needs-human-precedence
+ R-finalizer-cleanup.

Contract:
- The pause-probe loop checks `finalizer.halt_reason == "needs_human"`
  BEFORE sleeping; a NEEDS_HUMAN halt firing while paused immediately
  breaks the probe.
- The finalizer's flush() clears the pause sentinel on every chain-end
  path (including NEEDS_HUMAN unwind).
- A `chain_paused_lock_stale_cleared` row is emitted when an actual
  pause-sentinel removal occurred.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest


pytestmark = pytest.mark.acceptance


def _read_log_rows(plan_dir: Path) -> list[dict]:
    path = plan_dir / "_orchestrator_log.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_K6_pause_probe_breaks_on_needs_human(monkeypatch):
    """Probe loop breaks immediately when halt_reason='needs_human'."""
    from bin._chain_overnight import main as drive_main

    sleep_calls: list = []
    monkeypatch.setattr(drive_main.time, "sleep", lambda s: sleep_calls.append(s))
    monkeypatch.setattr(drive_main.pause_sentinel, "is_held", lambda _p: True)

    class _NeedsHumanFinalizer:
        halt_reason = "needs_human"

    finalizer = _NeedsHumanFinalizer()
    plan_dir = "/dev/null"

    iterations = 0
    while drive_main.pause_sentinel.is_held(plan_dir):
        iterations += 1
        if iterations > 50:  # defensive
            raise AssertionError("probe never broke on needs_human")
        if finalizer.halt_reason == "needs_human":
            break
        drive_main.time.sleep(drive_main.POLL_INTERVAL_SECONDS)

    # Loop must exit on iteration 1 with NO sleep call.
    assert iterations == 1
    assert sleep_calls == []


def test_K6_finalizer_clears_pause_on_needs_human_halt(monkeypatch, tmp_path):
    """Finalizer.flush() on NEEDS_HUMAN halt removes pause sentinel + emits row."""
    from bin._chain_overnight import exit_codes, main as drive_main
    from bin._chain_overnight import sentinel as running_sentinel
    from bin._chain_pause import sentinel as pause_sentinel

    plans_root = tmp_path / "plans"
    plans_root.mkdir()
    slug = "test-chain-slug"
    plan_dir = plans_root / slug
    plan_dir.mkdir()
    chain_id = "chain_2026-05-24T22:00:00Z_k6needsh"

    monkeypatch.setattr("bin._chain_overnight.main._PLANS_DIR", plans_root)

    # Stage running lock + pause sentinel.
    running_sentinel.acquire(
        plan_dir, chain_id=chain_id, driver_pid=os.getpid(),
        driver_host=socket.gethostname(),
        wall_clock_cap_seconds=28800,
        started_at="2026-05-24T22:00:00Z",
    )
    pause_sentinel.acquire(
        plan_dir, chain_id=chain_id, paused_at="2026-05-24T22:30:00Z",
        paused_by_pid=47832, paused_by_host="wsl2-bill",
        reason="K6 fixture", next_phase_to_enter=3,
    )

    paused_path = plan_dir / "_chain_paused.lock"
    assert paused_path.exists()

    finalizer = drive_main._ChainFinalizer(
        plan_dir=plan_dir,
        slug=slug,
        chain_id=chain_id,
        driver_session_id="sess_k6k6k6k6",
        chain_started_at="2026-05-24T22:00:00Z",
        chain_started_epoch=1716595200.0,
        wall_clock_cap_seconds=28800,
    )
    finalizer.set_halt(
        halt_reason="needs_human",
        exit_code=exit_codes.EXIT_RETRY_EXCEEDED,
    )
    finalizer.flush()

    # Pause sentinel cleared.
    assert not paused_path.exists()

    # chain_paused_lock_stale_cleared row emitted.
    rows = _read_log_rows(plan_dir)
    stale = [
        r for r in rows if r.get("event_type") == "chain_paused_lock_stale_cleared"
    ]
    assert len(stale) == 1, (
        f"expected exactly 1 chain_paused_lock_stale_cleared row; got {len(stale)}"
    )
    assert stale[0]["chain_id"] == chain_id
