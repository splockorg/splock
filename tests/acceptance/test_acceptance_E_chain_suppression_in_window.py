"""E.3 — `chain-suppression-block.sh` refuses suppression patterns when SPLOCK_PHASE == 5."""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


def _has_deny(stdout: str) -> bool:
    """Detect permissionDecision=deny in hook stdout.

    Note: chain-suppression-block.sh emits invalid JSON (embeds literal
    newlines inside string values for permissionDecisionReason). Pure
    json.loads() fails on its output. We fall back to substring matching
    on the canonical key — robust to that bug. See Pass 3 findings.
    """
    if not stdout.strip():
        return False
    try:
        payload = json.loads(stdout)
        if isinstance(payload, dict):
            if payload.get("permissionDecision") == "deny":
                return True
            hso = payload.get("hookSpecificOutput")
            if isinstance(hso, dict) and hso.get("permissionDecision") == "deny":
                return True
    except json.JSONDecodeError:
        pass
    # Substring fallback for hooks that emit broken JSON.
    return '"permissionDecision": "deny"' in stdout


def test_pytest_skip_refused_in_chain_phase_5(repo_root, hook_event_injector,
                                                pretool_use_event, chain_runtime_env):
    """E.3: `pytest.skip()` insertion refused during retry window."""
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
    env = chain_runtime_env()
    result = hook_event_injector(hook, event, env_overlay=env)
    assert result.returncode == 0
    assert _has_deny(result.stdout), (
        f"pytest.skip in SPLOCK_PHASE=5 retry window should be refused; "
        f"stdout={result.stdout!r}"
    )
