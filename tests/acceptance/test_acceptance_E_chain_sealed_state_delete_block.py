"""E.2 — `chain-sealed-state-delete-block.sh` refuses `rm`/`find -delete` on sealed paths."""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


def _has_deny(stdout: str) -> bool:
    if not stdout.strip():
        return False
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        for line in reversed(stdout.strip().splitlines()):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        else:
            return False
    if not isinstance(payload, dict):
        return False
    if payload.get("permissionDecision") == "deny":
        return True
    hso = payload.get("hookSpecificOutput")
    return isinstance(hso, dict) and hso.get("permissionDecision") == "deny"


def test_delete_refused_on_sealed_state_path(repo_root, hook_event_injector,
                                              pretool_use_event):
    """E.2a: Bash `rm` on sealed-state path is refused."""
    hook = repo_root / "hooks" / "chain-sealed-state-delete-block.sh"
    event = pretool_use_event(
        tool="Bash",
        tool_input={"command": "rm -f docs/plans/foo/_state.json"},
        cwd=str(repo_root),
    )
    result = hook_event_injector(hook, event)
    assert result.returncode == 0
    assert _has_deny(result.stdout), (
        f"Delete on sealed path should be refused; stdout={result.stdout!r}"
    )


def test_delete_permitted_on_non_sealed_path(repo_root, hook_event_injector,
                                              pretool_use_event):
    """E.2b: Bash `rm` on non-sealed path is permitted."""
    hook = repo_root / "hooks" / "chain-sealed-state-delete-block.sh"
    event = pretool_use_event(
        tool="Bash",
        tool_input={"command": "rm -f /tmp/some_user_file.txt"},
        cwd=str(repo_root),
    )
    result = hook_event_injector(hook, event)
    assert result.returncode == 0
    assert not _has_deny(result.stdout), (
        f"Non-sealed rm should be permitted; stdout={result.stdout!r}"
    )
