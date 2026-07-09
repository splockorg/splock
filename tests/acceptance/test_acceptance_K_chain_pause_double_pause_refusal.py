"""K3 — second `bin/chain-pause` invocation exits 23 (EXIT_ALREADY_PAUSED).

Per CCOR.1 implplan §T-9 + design_resolutions R-sentinel-primitive +
R-exit-codes.

Contract:
- After a successful pause, a second `bin/chain-pause` call must refuse
  with exit 23 (informative refusal, not silent noop).
- The original pause sentinel remains intact.
- A single `chain_paused` row exists in the log (the refused second
  call does NOT emit a row).
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest


pytestmark = pytest.mark.acceptance


@pytest.fixture
def staged_live_chain(monkeypatch, tmp_path):
    from bin._chain_overnight import manifest as manifest_mod
    from bin._chain_overnight import sentinel as running_sentinel

    plans_root = tmp_path / "plans"
    plans_root.mkdir()
    slug = "test-chain-slug"
    plan_dir = plans_root / slug
    plan_dir.mkdir()
    chain_id = "chain_2026-05-24T22:00:00Z_k3k3k3k3"

    monkeypatch.setattr("bin._chain_pause.main._PLANS_DIR", plans_root)

    running_sentinel.acquire(
        plan_dir, chain_id=chain_id, driver_pid=os.getpid(),
        driver_host=socket.gethostname(),
        wall_clock_cap_seconds=28800,
        started_at="2026-05-24T22:00:00Z",
    )
    manifest_mod.stamp_chain_start(
        plan_dir, slug=slug, chain_id=chain_id,
        chain_started_at="2026-05-24T22:00:00Z",
        wall_clock_cap_seconds=28800,
    )
    return {"plan_dir": plan_dir, "slug": slug, "chain_id": chain_id}


def _read_log_rows(plan_dir: Path) -> list[dict]:
    path = plan_dir / "_orchestrator_log.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_K3_second_pause_invocation_exits_23(staged_live_chain):
    """Second `bin/chain-pause` exits 23; first sentinel preserved."""
    from bin._chain_overnight import exit_codes
    from bin._chain_pause import main as pause_main

    plan_dir = staged_live_chain["plan_dir"]
    slug = staged_live_chain["slug"]
    paused_path = plan_dir / "_chain_paused.lock"

    rc1 = pause_main.main(["--slug", slug, "--reason", "first"])
    assert rc1 == exit_codes.EXIT_OK
    assert paused_path.exists()
    first_payload = paused_path.read_text(encoding="utf-8")

    rc2 = pause_main.main(["--slug", slug, "--reason", "second-should-refuse"])
    assert rc2 == exit_codes.EXIT_ALREADY_PAUSED, (
        f"second pause must exit {exit_codes.EXIT_ALREADY_PAUSED}; got {rc2}"
    )

    # First sentinel preserved (same contents).
    assert paused_path.exists()
    assert paused_path.read_text(encoding="utf-8") == first_payload, (
        "first pause sentinel was overwritten or modified"
    )

    # Exactly one chain_paused row.
    rows = _read_log_rows(plan_dir)
    paused_rows = [r for r in rows if r.get("event_type") == "chain_paused"]
    assert len(paused_rows) == 1, (
        f"expected exactly one chain_paused row (refused second emits none); "
        f"got {len(paused_rows)}"
    )


def test_K3_exit_code_23_is_canonical():
    """EXIT_ALREADY_PAUSED is allocated as 23 per R-exit-codes."""
    from bin._chain_overnight import exit_codes
    assert exit_codes.EXIT_ALREADY_PAUSED == 23
    assert exit_codes.EXIT_ALREADY_PAUSED in exit_codes.CHAIN_PAUSE_EMITTED_CODES
