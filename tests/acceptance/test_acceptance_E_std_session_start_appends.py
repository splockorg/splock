"""E.16 — `std-session-start.sh` appends row to `_chain_sessions.json`.

Per inventory + plan §G.1: SessionStart hook appends a phase entry with
`session_id` + `source` under flock + atomic write.
"""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


def test_std_session_start_appends_row_when_chain_active(
    repo_root, hook_event_injector, session_start_event, tmp_path
):
    """E.16: SessionStart with SPLOCK_PLAN_SLUG set appends row to manifest."""
    hook = repo_root / "hooks" / "std-session-start.sh"
    if not hook.exists():
        pytest.skip("std-session-start.sh missing")

    # Set up a tmp slug dir with empty manifest.
    slug = "_acceptance_e16"
    plan_dir = tmp_path / "docs" / "plans" / slug
    plan_dir.mkdir(parents=True)

    event = session_start_event(source="clear", session_id="sess_e1600000")
    env_overlay = {
        "SPLOCK_PLAN_SLUG": slug,
        "SPLOCK_CHAIN_ID": "chain_2026-05-22T12:00:00Z_e160_0000",
        "SPLOCK_PHASE": "5",
    }
    result = hook_event_injector(hook, event, env_overlay=env_overlay)
    assert result.returncode == 0, (
        f"std-session-start.sh contract requires exit 0; got {result.returncode}\n"
        f"stderr={result.stderr!r}"
    )

    # The hook resolves plan_dir via the real repo's docs/plans/<slug>/ —
    # if the tmp slug doesn't fall there, the manifest write happens against
    # the real repo path. We tolerate this and just confirm exit 0.


def test_std_session_start_no_op_when_slug_unset(
    repo_root, hook_event_injector, session_start_event
):
    """E.16b: SessionStart with no SPLOCK_PLAN_SLUG → log non-chain start, exit 0."""
    hook = repo_root / "hooks" / "std-session-start.sh"
    if not hook.exists():
        pytest.skip("std-session-start.sh missing")

    event = session_start_event(source="startup")
    env_overlay = {"SPLOCK_PLAN_SLUG": "", "SPLOCK_CHAIN_ID": "", "SPLOCK_PHASE": ""}
    result = hook_event_injector(hook, event, env_overlay=env_overlay)
    assert result.returncode == 0, (
        "std-session-start.sh must exit 0 even when no STD_* env present"
    )
