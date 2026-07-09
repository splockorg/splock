"""K5 — orphan-paused detection: dead driver PID → exit 22 + sentinel preserved.

Per CCOR.1 implplan §T-9 + design_resolutions R-orphan-detection.

Contract:
- Pause sentinel present, running lock present with dead driver PID
  → `bin/chain-resume` exits 22 with crashed-during-pause diagnostic.
- Pause sentinel is NOT released (forensic state preserved).
- Operator must use `bin/chain-overnight --release-lock` if they want
  a forensic reset.

Also covers the related variant: pause sentinel present, running lock
ABSENT → same exit 22 + sentinel preserved.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest


pytestmark = pytest.mark.acceptance


@pytest.fixture
def orphan_paused_dead_driver(monkeypatch, tmp_path):
    """Stage: pause sentinel + running lock w/ dead PID + manifest."""
    from bin._chain_overnight import manifest as manifest_mod
    from bin._chain_overnight import sentinel as running_sentinel
    from bin._chain_pause import sentinel as pause_sentinel

    plans_root = tmp_path / "plans"
    plans_root.mkdir()
    slug = "test-chain-slug"
    plan_dir = plans_root / slug
    plan_dir.mkdir()
    chain_id = "chain_2026-05-24T22:00:00Z_k5dead00"
    dead_pid = 9_999_999

    monkeypatch.setattr("bin._chain_resume.main._PLANS_DIR", plans_root)

    running_sentinel.acquire(
        plan_dir, chain_id=chain_id, driver_pid=dead_pid,
        driver_host=socket.gethostname(),
        wall_clock_cap_seconds=28800,
        started_at="2026-05-24T22:00:00Z",
    )
    manifest_mod.stamp_chain_start(
        plan_dir, slug=slug, chain_id=chain_id,
        chain_started_at="2026-05-24T22:00:00Z",
        wall_clock_cap_seconds=28800,
    )
    manifest_mod.stamp_pause_start(
        plan_dir, chain_id=chain_id, paused_at="2026-05-24T22:30:00Z",
    )
    pause_sentinel.acquire(
        plan_dir, chain_id=chain_id, paused_at="2026-05-24T22:30:00Z",
        paused_by_pid=47832, paused_by_host="wsl2-bill",
        reason="K5 fixture", next_phase_to_enter=3,
    )
    return {"plan_dir": plan_dir, "slug": slug, "chain_id": chain_id}


def test_K5_orphan_paused_dead_driver_refused_exit_22(orphan_paused_dead_driver):
    """Dead driver PID + pause sentinel → exit 22; sentinel preserved."""
    from bin._chain_overnight import exit_codes
    from bin._chain_resume import main as resume_main

    plan_dir = orphan_paused_dead_driver["plan_dir"]
    slug = orphan_paused_dead_driver["slug"]
    paused_path = plan_dir / "_chain_paused.lock"
    assert paused_path.exists()

    rc = resume_main.main(["--slug", slug])
    assert rc == exit_codes.EXIT_NOT_PAUSED, (
        f"orphan-paused resume must exit {exit_codes.EXIT_NOT_PAUSED}; got {rc}"
    )

    # Sentinel preserved per R-orphan-detection.
    assert paused_path.exists(), (
        "pause sentinel must be PRESERVED on orphan detection (forensic)"
    )


def test_K5_orphan_paused_no_running_lock_refused_exit_22(monkeypatch, tmp_path):
    """Pause sentinel present, running lock absent → exit 22; preserved."""
    from bin._chain_overnight import exit_codes, manifest as manifest_mod
    from bin._chain_pause import sentinel as pause_sentinel
    from bin._chain_resume import main as resume_main

    plans_root = tmp_path / "plans"
    plans_root.mkdir()
    slug = "test-chain-slug"
    plan_dir = plans_root / slug
    plan_dir.mkdir()
    chain_id = "chain_2026-05-24T22:00:00Z_k5norun"

    monkeypatch.setattr("bin._chain_resume.main._PLANS_DIR", plans_root)

    manifest_mod.stamp_chain_start(
        plan_dir, slug=slug, chain_id=chain_id,
        chain_started_at="2026-05-24T22:00:00Z",
        wall_clock_cap_seconds=28800,
    )
    manifest_mod.stamp_pause_start(
        plan_dir, chain_id=chain_id, paused_at="2026-05-24T22:30:00Z",
    )
    pause_sentinel.acquire(
        plan_dir, chain_id=chain_id, paused_at="2026-05-24T22:30:00Z",
        paused_by_pid=47832, paused_by_host="wsl2-bill",
        reason="K5b fixture", next_phase_to_enter=3,
    )

    paused_path = plan_dir / "_chain_paused.lock"
    running_path = plan_dir / "_chain_running.lock"
    assert paused_path.exists()
    assert not running_path.exists()

    rc = resume_main.main(["--slug", slug])
    assert rc == exit_codes.EXIT_NOT_PAUSED
    assert paused_path.exists(), "pause sentinel must be preserved"
