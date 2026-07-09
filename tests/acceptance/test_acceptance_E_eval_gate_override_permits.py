"""E.15 — `EVAL_GATE_OVERRIDE=1` + reason permits commit on regression.

Loud-logs to _orchestrator_log.jsonl. Reason must be non-empty.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def test_eval_gate_override_with_reason_permits(
    repo_root, hook_event_injector, pretool_use_event
):
    """E.15: with override=1 + reason set, hook should permit even regressing diffs."""
    hook = repo_root / "hooks" / "eval-gate-pre-commit.sh"
    if not hook.exists():
        pytest.skip("eval-gate-pre-commit.sh missing")

    event = pretool_use_event(
        tool="Edit",
        tool_input={
            "file_path": "src/scoring.py",
            "new_string": "def score(x): return x",
            "old_string": "def score(x): return x * 2",
        },
        cwd=str(repo_root),
    )
    env_overlay = {
        "EVAL_GATE_OVERRIDE": "1",
        "EVAL_GATE_OVERRIDE_REASON": "investigating scoring regression suspected",
        "SPLOCK_CHAIN_ID": "",  # interactive mode (override only applies there)
    }
    result = hook_event_injector(hook, event, env_overlay=env_overlay)
    # With override + reason, hook should not refuse with exit 32.
    assert result.returncode != 32, (
        f"eval-gate-pre-commit.sh refused (32) despite EVAL_GATE_OVERRIDE=1 + reason"
    )
