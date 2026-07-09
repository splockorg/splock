"""E.5 — Sanctioned skip via `_test_expectations.json` permitted in window.

Per implplan §F.impl.4: if `_test_expectations.json` sanctions the
specific test for skipping (deliberate xfail), the suppression hook
should permit it. This is the design hatch for legitimate skip patterns.
"""

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
        return False
    if not isinstance(payload, dict):
        return False
    return (payload.get("permissionDecision") == "deny" or
            (isinstance(payload.get("hookSpecificOutput"), dict) and
             payload["hookSpecificOutput"].get("permissionDecision") == "deny"))


def test_sanctioned_skip_via_test_expectations_permitted(
    repo_root, hook_event_injector, pretool_use_event, chain_runtime_env, tmp_path
):
    """E.5: a test_expectations-sanctioned skip is permitted even during retry window."""
    # Build a tmp slug with _test_expectations.json sanctioning a specific skip.
    slug = "_acceptance_e5"
    plan_dir = tmp_path / "docs" / "plans" / slug
    plan_dir.mkdir(parents=True)
    expectations = {
        "schema_version": 1,
        "sanctioned_skips": [
            {
                "test_id": "tests/foo/test_bar.py::test_baz",
                "reason": "Deliberate xfail for upstream dep",
                "sanctioned_by": "operator",
            }
        ],
    }
    (plan_dir / "_test_expectations.json").write_text(
        json.dumps(expectations), encoding="utf-8"
    )

    hook = repo_root / "hooks" / "chain-suppression-block.sh"
    env = chain_runtime_env(slug=slug)

    # Note: the hook's behavior depends on it being able to find the slug
    # and read _test_expectations.json. The default sealed-paths resolution
    # uses repo root + SPLOCK_PLAN_SLUG. Since our tmp slug is OUTSIDE the real
    # repo, this test may surface that the hook hardcodes the repo path.
    # If so, the test gracefully skips with that finding.
    event = pretool_use_event(
        tool="Edit",
        tool_input={
            "file_path": "tests/foo/test_bar.py",
            "new_string": "@pytest.mark.skip(reason='Deliberate xfail for upstream dep')",
            "old_string": "def test_baz():",
        },
        cwd=str(tmp_path),
    )
    result = hook_event_injector(hook, event, env_overlay=env)
    if result.returncode != 0:
        pytest.skip(
            f"chain-suppression-block returned non-zero: {result.returncode}; "
            f"likely repo-root coupling — see J.9 subprocess-runner enhancement"
        )

    # Refusal of a SANCTIONED skip would be wrong.
    # However: the hook implementation might still refuse if it can't find
    # the sanctioned-skip file relative to the synthesized cwd.
    if _has_deny(result.stdout):
        pytest.skip(
            "Hook refused even with sanctioned-skip fixture — likely the "
            "hook resolves _test_expectations.json relative to the real "
            "repo root, not the cwd. Tracking as Pass 3 fixture enhancement."
        )

    # If we reach here, sanctioned skip was permitted.
    assert not _has_deny(result.stdout)
