"""K8 — `--from-resume` consumes inject at startup; first phase spawn gets it.

Per CCOR.1 implplan §T-9 + design_resolutions R-from-resume-symmetry.

Contract:
- Pre-stage `_operator_inject.md` in a plan-dir.
- On `bin/chain-overnight --from-resume`, the driver calls
  `_consume_operator_inject_if_present` at startup BEFORE entering the
  phase loop.
- The framed body is propagated into `_drive_phases` via the
  `startup_inject_text` kwarg.
- The file is deleted after consume; the first phase spawn after
  recovery receives the inject.
"""

from __future__ import annotations

import inspect
import os
import socket
from pathlib import Path

import pytest


pytestmark = pytest.mark.acceptance


def _stage_operator_inject(plan_dir: Path, text: str) -> Path:
    framed = (
        "<!-- operator-inject schema=1 written_at=2026-05-24T22:30:00Z -->\n"
        f"<operator-inject>\n{text}\n</operator-inject>\n"
    )
    target = plan_dir / "_operator_inject.md"
    target.write_text(framed, encoding="utf-8")
    return target


def test_K8_run_chain_consumes_inject_on_from_resume_startup():
    """Source-level invariant: `_run_chain` calls
    `_consume_operator_inject_if_present` inside an `if args.from_resume:` block,
    BEFORE invoking `_drive_phases`.
    """
    from bin._chain_overnight import main as drive_main

    src = inspect.getsource(drive_main._run_chain)
    assert "if args.from_resume:" in src
    assert "_consume_operator_inject_if_present" in src
    consume_idx = src.index("_consume_operator_inject_if_present")
    drive_idx = src.index("_drive_phases(")
    assert consume_idx < drive_idx, (
        "Startup consume must occur BEFORE _drive_phases (the phase-loop entry)"
    )


def test_K8_drive_phases_signature_accepts_startup_inject_text():
    """`_drive_phases` accepts a `startup_inject_text` kwarg threading
    the consumed body into the first phase spawn.
    """
    from bin._chain_overnight import main as drive_main

    sig = inspect.signature(drive_main._drive_phases)
    assert "startup_inject_text" in sig.parameters


def test_K8_startup_consume_deletes_inject_file(monkeypatch, tmp_path):
    """End-to-end (helper level): a staged inject is read + deleted
    by the startup-consume helper that `--from-resume` invokes.
    """
    from bin._chain_overnight import main as drive_main
    from bin._chain_overnight import sentinel as running_sentinel
    from bin._chain_overnight import manifest as manifest_mod

    plans_root = tmp_path / "plans"
    plans_root.mkdir()
    slug = "test-chain-slug"
    plan_dir = plans_root / slug
    plan_dir.mkdir()
    chain_id = "chain_2026-05-24T22:00:00Z_k8k8k8k8"

    monkeypatch.setattr("bin._chain_overnight.main._PLANS_DIR", plans_root)
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

    inject_path = _stage_operator_inject(plan_dir, "K8 from-resume body")
    assert inject_path.exists()

    body = drive_main._consume_operator_inject_if_present(
        plan_dir, chain_id, slug=slug,
    )
    assert body is not None
    assert "K8 from-resume body" in body
    assert "<operator-inject>" in body
    assert not inject_path.exists(), (
        "consume helper must DELETE the inject after read (single-shot)"
    )


def test_K8_pending_inject_threaded_into_first_phase_spawn():
    """Source-level invariant: `_drive_phases` uses `_pending_startup_inject`
    as a fallback when per-phase consume returns None, and clears it after use.
    """
    from bin._chain_overnight import main as drive_main

    src = inspect.getsource(drive_main._drive_phases)
    assert "_pending_startup_inject" in src
    assert "_pending_startup_inject = None" in src
    assert "if inject_text is None and _pending_startup_inject is not None:" in src
