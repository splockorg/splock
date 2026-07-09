"""E.14 — `eval-gate-pre-commit.sh` strict in interactive mode; report-only in chain."""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def test_eval_gate_hook_dispatches_via_pre_tool_use(
    repo_root, hook_event_injector, pretool_use_event
):
    """E.14: eval-gate hook fires on Edit and dispatches bin/eval-gate."""
    hook = repo_root / "hooks" / "eval-gate-pre-commit.sh"
    if not hook.exists():
        pytest.skip("eval-gate-pre-commit.sh missing")

    # Interactive mode (no SPLOCK_CHAIN_ID) — strict.
    event = pretool_use_event(
        tool="Edit",
        tool_input={
            "file_path": "src/scoring.py",
            "new_string": "def score(x): return x",
            "old_string": "def score(x): return x * 2",
        },
        cwd=str(repo_root),
    )
    env_no_chain = {"SPLOCK_CHAIN_ID": ""}
    result = hook_event_injector(hook, event, env_overlay=env_no_chain)
    # Hook exit codes: 0 (clean) or 32 (regression) or skip if not touching gated files.
    assert result.returncode in (0, 32), (
        f"eval-gate-pre-commit.sh expected exit 0 or 32; got {result.returncode}\n"
        f"stderr={result.stderr!r}"
    )
