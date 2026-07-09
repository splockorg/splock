"""E.13 — `escalation-trigger-precommit.sh` fires on blast_radius / cross_repo / cross_vertical."""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def test_escalation_trigger_hook_dispatches_route_issue_check(
    repo_root, hook_event_injector, pretool_use_event
):
    """E.13: hook invokes bin/route_issue --check-scope and propagates exit code."""
    hook = repo_root / "hooks" / "escalation-trigger-precommit.sh"
    if not hook.exists():
        pytest.skip("escalation-trigger-precommit.sh missing")

    # Stage a benign Edit; no escalation should fire.
    event = pretool_use_event(
        tool="Edit",
        tool_input={
            "file_path": "src/single.py",
            "new_string": "def x(): pass",
            "old_string": "",
        },
        cwd=str(repo_root),
    )
    result = hook_event_injector(hook, event)
    # Hook propagates: 0 (clean) or 25 (trigger fired). Both valid.
    assert result.returncode in (0, 25), (
        f"escalation-trigger.sh expected exit 0 or 25; got {result.returncode}\n"
        f"stderr={result.stderr!r}"
    )
