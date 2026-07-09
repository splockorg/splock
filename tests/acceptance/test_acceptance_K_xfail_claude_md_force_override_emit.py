"""K.8 (xfail) — `claude-md-discipline.sh` `[force-claude-md]` emits to per-plan log.

Per inventory + §10 §7.4 (§M.impl.3 spec clarification):
when `[force-claude-md]` downgrades the refusal to a warning, the
forensic row should land in the active plan's `_orchestrator_log.jsonl`
in addition to (or instead of) the per-user hook_log.jsonl. Pre-fix,
only the per-user audit log is emitted to.

Distinct from K.5 (which checks the static source for per-plan
references): K.8 verifies the runtime behavior end-to-end by simulating
a `[force-claude-md]` commit and asserting the per-plan log gains a row.
"""

from __future__ import annotations

import json
import os
import subprocess
import pytest
from pathlib import Path


pytestmark = pytest.mark.acceptance


@pytest.mark.xfail(
    reason="§M F-03 per-plan force_override JSONL emission pending per §M.impl.3 clarification",
    strict=False,
)
def test_force_claude_md_token_emits_to_per_plan_orchestrator_log(tmp_repo, monkeypatch):
    """K.8: [force-claude-md] commit produces a row in per-plan _orchestrator_log.jsonl.

    Skipped if the hook can't be invoked in isolation (it depends on a real
    git pre-commit context). Post-fix, the hook should be invokable with
    a `--simulate` flag or similar.
    """
    hook_path = Path(__file__).resolve().parents[2] / "hooks" / "claude-md-discipline.sh"
    if not hook_path.exists():
        pytest.skip("claude-md-discipline.sh missing — can't exercise")

    # Pre-fix: the hook only emits to ~/.claude/logs/. Post-fix: should also
    # emit to docs/plans/<slug>/_orchestrator_log.jsonl when slug resolvable.
    slug_dir = tmp_repo / "docs" / "plans" / "_acceptance_k8"
    slug_dir.mkdir(parents=True)

    # Simulate a force-claude-md context: SPLOCK_PLAN_SLUG hint + commit msg
    # via env. Pre-fix the hook ignores SPLOCK_PLAN_SLUG; post-fix, should
    # consult it to resolve the per-plan log target.
    env = os.environ.copy()
    env.update({
        "SPLOCK_PLAN_SLUG": "_acceptance_k8",
        "GIT_DIR": str(tmp_repo / ".git"),
        "CLAUDE_HOOK_COMMIT_MSG": "test commit [force-claude-md]",
    })

    # Best-effort invocation — the hook's exact harness is operator-context
    # dependent. We exercise the hook entry directly; assert per-plan log
    # gained a row.
    try:
        subprocess.run(
            ["bash", str(hook_path)],
            input="",
            env=env,
            capture_output=True,
            timeout=10,
            text=True,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pytest.skip("Hook invocation harness not available")

    per_plan_log = slug_dir / "_orchestrator_log.jsonl"
    assert per_plan_log.exists(), (
        "Per-plan _orchestrator_log.jsonl not created — F-03 fix pending"
    )
    rows = [json.loads(l) for l in per_plan_log.read_text().splitlines() if l.strip()]
    assert any(
        "claude-md" in str(r).lower() or "force" in str(r).lower()
        for r in rows
    ), "No claude-md force-override row in per-plan log — F-03 fix pending"
