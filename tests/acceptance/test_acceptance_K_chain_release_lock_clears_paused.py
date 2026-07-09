"""K7 — `bin/chain-overnight --release-lock` clears BOTH running + pause locks.

Per CCOR.1 implplan §T-9 + design_resolutions R-release-lock-pause.

Contract:
- `--release-lock` is enhanced to ALSO remove `_chain_paused.lock` if
  present (in addition to its existing removal of `_chain_running.lock`).
- On actual removal of the pause sentinel, a
  `chain_paused_lock_stale_cleared` row is emitted.
- Exit OK.
"""

from __future__ import annotations

import json
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


def test_K7_release_lock_removes_both_locks_and_emits_row(monkeypatch, tmp_path):
    """Both locks cleared; stale-cleared row emitted with chain_id."""
    from bin._chain_overnight import exit_codes, main as drive_main
    from bin._chain_overnight import sentinel as running_sentinel
    from bin._chain_pause import sentinel as pause_sentinel

    plans_root = tmp_path / "plans"
    plans_root.mkdir()
    slug = "test-chain-slug"
    plan_dir = plans_root / slug
    plan_dir.mkdir()
    chain_id = "chain_2026-05-24T22:00:00Z_k7k7k7k7"

    monkeypatch.setattr("bin._chain_overnight.main._PLANS_DIR", plans_root)

    # Use a dead-PID running lock so --release-lock doesn't refuse it
    # for being live.
    running_sentinel.acquire(
        plan_dir, chain_id=chain_id, driver_pid=9_999_999,
        driver_host=socket.gethostname(),
        wall_clock_cap_seconds=28800,
        started_at="2026-05-24T22:00:00Z",
    )
    pause_sentinel.acquire(
        plan_dir, chain_id=chain_id, paused_at="2026-05-24T22:30:00Z",
        paused_by_pid=47832, paused_by_host="wsl2-bill",
        reason="K7 fixture", next_phase_to_enter=3,
    )

    running_path = plan_dir / "_chain_running.lock"
    paused_path = plan_dir / "_chain_paused.lock"
    assert running_path.exists()
    assert paused_path.exists()

    rc = drive_main.main(["--release-lock", chain_id, slug])
    assert rc == exit_codes.EXIT_OK

    # Both locks gone.
    assert not running_path.exists()
    assert not paused_path.exists()

    # Stale-cleared row emitted.
    rows = _read_log_rows(plan_dir)
    stale = [
        r for r in rows if r.get("event_type") == "chain_paused_lock_stale_cleared"
    ]
    assert len(stale) == 1
    assert stale[0]["chain_id"] == chain_id
