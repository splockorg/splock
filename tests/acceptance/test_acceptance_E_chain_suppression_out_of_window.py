"""E.4 — `chain-suppression-block.sh` no-ops when SPLOCK_PHASE != 5."""

from __future__ import annotations

import json
import os
import pytest


pytestmark = pytest.mark.acceptance


def _has_deny(stdout: str) -> bool:
    if not stdout.strip():
        return False
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    return (payload.get("permissionDecision") == "deny" or
            (isinstance(payload.get("hookSpecificOutput"), dict) and
             payload["hookSpecificOutput"].get("permissionDecision") == "deny"))


def test_pytest_skip_permitted_outside_chain_phase_5(
    repo_root, hook_event_injector, pretool_use_event
):
    """E.4: `pytest.skip()` permitted in interactive session (no SPLOCK_PHASE=5)."""
    hook = repo_root / "hooks" / "chain-suppression-block.sh"
    event = pretool_use_event(
        tool="Edit",
        tool_input={
            "file_path": "src/something.py",
            "new_string": "pytest.skip('not implemented')",
            "old_string": "do_thing()",
        },
        cwd=str(repo_root),
    )
    # Explicitly clear SPLOCK_PHASE so we're out of window.
    env_overlay = {k: "" for k in ("SPLOCK_PLAN_SLUG", "SPLOCK_CHAIN_ID", "SPLOCK_PHASE")}
    result = hook_event_injector(hook, event, env_overlay=env_overlay)
    assert result.returncode == 0
    assert not _has_deny(result.stdout), (
        f"pytest.skip should be permitted outside SPLOCK_PHASE=5; "
        f"stdout={result.stdout!r}"
    )
