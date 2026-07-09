"""K1 — chain pause + resume + inject happy-path end-to-end.

Per CCOR.1 implplan §T-9 + design_resolutions R-granularity + R-inject-wiring
+ R-inject-size.

Contract:
- Stage a live chain (running lock + manifest, no pause sentinel).
- Invoke `bin.chain-pause` via the underlying CLI entry point: pause
  sentinel acquired, `chain_paused` log row emitted, exit 0.
- Invoke `bin.chain-resume --inject "Try strategy X"`: pause sentinel
  released, `_operator_inject.md` written with framed body, `chain_resumed`
  log row emitted, exit 0.
- The `_consume_operator_inject_if_present` helper (which the driver
  calls at the next phase boundary) reads + deletes the file and returns
  the framed body containing `<operator-inject>Try strategy X</operator-inject>`.

This validates the FULL operator workflow without spawning a real
chain driver: pause → resume-with-inject → consume.
"""

from __future__ import annotations

import json
import os
import socket
import sys
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


@pytest.fixture
def staged_live_chain(monkeypatch, tmp_path):
    """Stage a fully-live chain (running lock + manifest, no pause)."""
    from bin._chain_overnight import manifest as manifest_mod
    from bin._chain_overnight import sentinel as running_sentinel

    plans_root = tmp_path / "plans"
    plans_root.mkdir()
    slug = "test-chain-slug"
    plan_dir = plans_root / slug
    plan_dir.mkdir()
    chain_id = "chain_2026-05-24T22:00:00Z_kkk11111"

    # Redirect both CLIs' _PLANS_DIR.
    monkeypatch.setattr("bin._chain_pause.main._PLANS_DIR", plans_root)
    monkeypatch.setattr("bin._chain_resume.main._PLANS_DIR", plans_root)

    running_sentinel.acquire(
        plan_dir,
        chain_id=chain_id,
        driver_pid=os.getpid(),
        driver_host=socket.gethostname(),
        wall_clock_cap_seconds=28800,
        started_at="2026-05-24T22:00:00Z",
    )
    manifest_mod.stamp_chain_start(
        plan_dir,
        slug=slug,
        chain_id=chain_id,
        chain_started_at="2026-05-24T22:00:00Z",
        wall_clock_cap_seconds=28800,
    )
    return {"plan_dir": plan_dir, "slug": slug, "chain_id": chain_id}


def test_K1_pause_then_resume_with_inject_happy_path(staged_live_chain):
    """K1: pause → resume --inject → consume; verify each artifact."""
    from bin._chain_overnight import main as drive_main
    from bin._chain_overnight import exit_codes
    from bin._chain_pause import main as pause_main
    from bin._chain_pause import sentinel as pause_sentinel
    from bin._chain_resume import main as resume_main

    plan_dir = staged_live_chain["plan_dir"]
    slug = staged_live_chain["slug"]
    chain_id = staged_live_chain["chain_id"]

    paused_path = plan_dir / "_chain_paused.lock"
    inject_path = plan_dir / "_operator_inject.md"

    # Step 1: pause.
    rc = pause_main.main(["--slug", slug, "--reason", "K1 happy path"])
    assert rc == exit_codes.EXIT_OK, f"pause should exit 0; got {rc}"
    assert paused_path.exists()

    rows_after_pause = _read_log_rows(plan_dir)
    paused_rows = [r for r in rows_after_pause if r.get("event_type") == "chain_paused"]
    assert len(paused_rows) == 1, (
        f"expected exactly one chain_paused row, got {len(paused_rows)}"
    )
    assert paused_rows[0]["chain_id"] == chain_id

    # The DRIVER would call stamp_pause_start when honoring the pause
    # sentinel at the next phase boundary. Simulate that here so resume's
    # stamp_pause_end has an active pause to close (per R-stamp-before-release).
    from bin._chain_overnight import manifest as manifest_mod
    manifest_mod.stamp_pause_start(
        plan_dir, chain_id=chain_id, paused_at="2026-05-24T22:30:00Z",
    )

    # Step 2: resume with inject.
    rc = resume_main.main(["--slug", slug, "--inject", "Try strategy X"])
    assert rc == exit_codes.EXIT_OK, f"resume should exit 0; got {rc}"
    assert not paused_path.exists(), "pause sentinel should be released"
    assert inject_path.exists(), "_operator_inject.md should be present"

    rows_after_resume = _read_log_rows(plan_dir)
    resumed_rows = [r for r in rows_after_resume if r.get("event_type") == "chain_resumed"]
    assert len(resumed_rows) == 1, (
        f"expected exactly one chain_resumed row, got {len(resumed_rows)}"
    )

    # Verify inject body framing.
    body = inject_path.read_text(encoding="utf-8")
    assert "<operator-inject>" in body
    assert "Try strategy X" in body
    assert "</operator-inject>" in body

    # Step 3: driver-side consume — emulate the phase-boundary consume.
    consumed = drive_main._consume_operator_inject_if_present(
        plan_dir, chain_id, slug=slug,
    )
    assert consumed is not None
    assert "Try strategy X" in consumed
    assert "<operator-inject>" in consumed
    assert not inject_path.exists(), (
        "_consume_operator_inject_if_present must DELETE after read (single-shot)"
    )

    # And a pause_inject_consumed row should be present from the consume.
    rows_final = _read_log_rows(plan_dir)
    consume_rows = [
        r for r in rows_final if r.get("event_type") == "pause_inject_consumed"
    ]
    assert len(consume_rows) == 1, (
        f"expected exactly one pause_inject_consumed row, got {len(consume_rows)}"
    )
