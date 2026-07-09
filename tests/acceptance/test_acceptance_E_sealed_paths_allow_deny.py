"""E.1 — `sealed-paths.sh` refuses Write to sealed-state; permits non-sealed.

Per inventory:
- Source: userguide §5.1 (always-on hooks).
- Predecessor: synthetic PreToolUse event JSON piped to the actual hook
  script via stdin.
- Expected outcome:
  - Sealed-path target (e.g., `docs/plans/foo/_state.json`) → JSON
    `permissionDecision: "deny"` on stdout; exit 0 (hook design per
    plan §G.7.2).
  - Non-sealed target (e.g., `docs/plans/foo/notes.md`) → no deny;
    exit 0.
"""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


def _has_deny(stdout: str) -> bool:
    """Best-effort parse of hook stdout for permissionDecision: deny.

    Hook stdout convention per plan §G.2a: JSON object with
    `hookSpecificOutput.permissionDecision: "deny"` (canonical envelope)
    OR legacy flat `permissionDecision: "deny"`.
    """
    if not stdout.strip():
        return False
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        # Some hook scripts emit multi-line; try last non-blank line.
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
    if isinstance(hso, dict) and hso.get("permissionDecision") == "deny":
        return True
    return False


def test_sealed_paths_refuses_state_json_write(
    repo_root, hook_event_injector, pretool_use_event
):
    """E.1a: Write to a sealed-state path is refused."""
    hook = repo_root / "hooks" / "sealed-paths.sh"
    assert hook.exists(), f"Missing hook script: {hook}"

    event = pretool_use_event(
        tool="Write",
        tool_input={
            "file_path": "docs/plans/_acceptance_test/_state.json",
            "content": '{"task": "anything"}',
        },
        cwd=str(repo_root),
    )
    result = hook_event_injector(hook, event)

    assert result.returncode == 0, (
        f"sealed-paths.sh expected exit 0 (uses stdout deny envelope); "
        f"got {result.returncode}\nstderr: {result.stderr}"
    )
    assert _has_deny(result.stdout), (
        "Expected permissionDecision=deny for sealed-state Write; "
        f"got stdout={result.stdout!r}"
    )


def test_sealed_paths_permits_non_sealed_write(
    repo_root, hook_event_injector, pretool_use_event
):
    """E.1b: Write to a non-sealed path is permitted."""
    hook = repo_root / "hooks" / "sealed-paths.sh"

    event = pretool_use_event(
        tool="Write",
        tool_input={
            "file_path": "docs/plans/_acceptance_test/notes.md",
            "content": "Just notes.",
        },
        cwd=str(repo_root),
    )
    result = hook_event_injector(hook, event)

    assert result.returncode == 0, (
        f"sealed-paths.sh expected exit 0; got {result.returncode}\n"
        f"stderr: {result.stderr}"
    )
    assert not _has_deny(result.stdout), (
        "Expected no deny for non-sealed Write; "
        f"got stdout={result.stdout!r}"
    )
