"""K.5 (xfail) — §M F-03 `[force-claude-md]` force_override JSONL emission.

Per inventory + §10 §7.4 (§M.impl.3 spec clarification):
the `claude-md-discipline.sh` pre-commit hook should emit a forensic row
to per-plan `_orchestrator_log.jsonl` when `[force-claude-md]` token
downgrades a refusal to a warning. Architectural tension: the git
pre-commit hook doesn't know the active plan slug. Currently emits only
to `~/.claude/logs/hook_log.jsonl` (audit surface).

When the slug-resolution mechanism + per-plan emit ships, this xpasses.
"""

from __future__ import annotations

import pytest
import re


pytestmark = pytest.mark.acceptance


@pytest.mark.xfail(
    reason="§M F-03 force_override per-plan JSONL emission pending per §M.impl.3 clarification",
    strict=False,
)
def test_claude_md_discipline_emits_to_per_plan_log_on_force_override(repo_root):
    """K.5: claude-md-discipline.sh references per-plan log path on force-override branch.

    We grep the hook source for evidence of per-plan log emission. If the
    hook only references the hook_log.jsonl audit surface, the per-plan
    emission has not landed.
    """
    hook_path = repo_root / "hooks" / "claude-md-discipline.sh"
    text = hook_path.read_text(encoding="utf-8")

    # Pre-fix: only hook_log.jsonl reference (audit surface).
    # Post-fix: should reference docs/plans/<slug>/_orchestrator_log.jsonl
    # OR call bin/update_orchestrator / bin/log with slug resolution.
    per_plan_indicator = re.search(
        r"docs/plans/[^/]+/_orchestrator_log\.jsonl|"
        r"bin/update_orchestrator|"
        r"bin/log\s.*--slug",
        text,
    )
    assert per_plan_indicator is not None, (
        "claude-md-discipline.sh has no per-plan log emission path — "
        "F-03 fix pending"
    )
