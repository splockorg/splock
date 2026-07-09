"""E.6 — `chain-test-file-edit-flag.sh` PostToolUse appends to staging file, never refuses."""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


def test_post_tool_use_flag_hook_never_refuses(
    repo_root, hook_event_injector, posttool_use_event, chain_runtime_env
):
    """E.6: PostToolUse hook contract — never refuses, always exit 0."""
    hook = repo_root / "hooks" / "chain-test-file-edit-flag.sh"
    event = posttool_use_event(
        tool="Edit",
        tool_input={
            "file_path": "tests/foo/test_baz.py",
            "new_string": "def test_new(): pass",
            "old_string": "def test_old(): pass",
        },
        tool_response={"success": True},
        cwd=str(repo_root),
    )
    env = chain_runtime_env()
    result = hook_event_injector(hook, event, env_overlay=env)
    assert result.returncode == 0, (
        f"PostToolUse hook violated never-refuse contract; rc={result.returncode}"
    )
    # PostToolUse hooks must not emit permissionDecision deny per R-POSTTOOL-NO-DENY.
    if result.stdout.strip():
        try:
            payload = json.loads(result.stdout)
            assert payload.get("permissionDecision") != "deny", (
                "PostToolUse hook emitted permissionDecision=deny — "
                "violates R-POSTTOOL-NO-DENY"
            )
        except json.JSONDecodeError:
            pass  # Non-JSON stdout is acceptable for log-only hooks
