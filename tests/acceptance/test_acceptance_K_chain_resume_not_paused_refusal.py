"""K4 — `bin/chain-resume` with no pause sentinel exits 22 (EXIT_NOT_PAUSED).

Per CCOR.1 implplan §T-9 + design_resolutions R-exit-codes +
R-orphan-detection.

Contract:
- A plan-dir without `_chain_paused.lock` cannot be resumed.
- `bin/chain-resume --slug X` returns exit 22.
- No state mutations occur.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest


pytestmark = pytest.mark.acceptance


def test_K4_resume_without_sentinel_exits_22(monkeypatch, tmp_path):
    """No pause sentinel → resume exits 22."""
    from bin._chain_overnight import exit_codes
    from bin._chain_resume import main as resume_main

    plans_root = tmp_path / "plans"
    plans_root.mkdir()
    slug = "test-chain-slug"
    plan_dir = plans_root / slug
    plan_dir.mkdir()
    monkeypatch.setattr("bin._chain_resume.main._PLANS_DIR", plans_root)

    paused_path = plan_dir / "_chain_paused.lock"
    assert not paused_path.exists()

    rc = resume_main.main(["--slug", slug])
    assert rc == exit_codes.EXIT_NOT_PAUSED, (
        f"resume on un-paused chain must exit {exit_codes.EXIT_NOT_PAUSED}; got {rc}"
    )

    # No state mutations.
    assert not paused_path.exists()
    inject_path = plan_dir / "_operator_inject.md"
    assert not inject_path.exists()


def test_K4_exit_code_22_is_canonical():
    """EXIT_NOT_PAUSED is allocated as 22 per R-exit-codes."""
    from bin._chain_overnight import exit_codes
    assert exit_codes.EXIT_NOT_PAUSED == 22
    assert exit_codes.EXIT_NOT_PAUSED in exit_codes.CHAIN_RESUME_EMITTED_CODES
