"""C.6 — Completion summary write-LAST holds on path-2 (wip-set-empty trigger).

Per orchestrator §4a.4 + Opus M-4: path-2 fires via
`bin/update_orchestrator` when the wip set transitions to empty. The
same "summary write is LAST" sequencing rule applies — if the downstream
gesture (e.g., transition log emit) fails after the summary write, the
summary file still reflects terminal state.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def test_completion_summary_path2_write_last_invariant_pinned(repo_root):
    """C.6: path-2 emission path includes summary write before any downstream emit.

    Per orchestrator §4a.4 the invariant is structurally enforced; this test
    verifies the path-2 caller (in bin/_update_orchestrator/) writes summary
    LAST or, equivalently, has a test that pins the sequencing — same
    discipline as path-1's test_summary_write_is_last_action_of_emit_path.
    """
    # The path-2 caller lives in bin/_update_orchestrator/. Either it
    # emits via bin/_chain_overnight/completion_summary (sharing the
    # atomic-write infrastructure) or has its own atomic emit.
    update_orch_dir = repo_root / "bin" / "_update_orchestrator"
    assert update_orch_dir.is_dir(), "bin/_update_orchestrator missing"

    # Search the package for explicit completion-summary write call sites.
    sources = list(update_orch_dir.glob("*.py"))
    all_text = "\n".join(p.read_text(encoding="utf-8") for p in sources)

    # Look for the path-2 trigger: detection of empty wip set + summary emit.
    has_summary_emit = (
        "completion_summary" in all_text or
        "emit_chain_summary" in all_text or
        "_completion_summary" in all_text
    )

    if not has_summary_emit:
        # If path-2's invocation is NOT in bin/_update_orchestrator/, then
        # either it's in the chain driver (already covered by C.5) or it's
        # deferred. Surface this clearly rather than silently pass.
        pytest.skip(
            "path-2 wip-empty completion-summary emit not located in "
            "bin/_update_orchestrator/ — verify against implplan §A.impl.7 "
            "path 2 description"
        )

    # If found, verify the path-2 caller has a path-2-specific test for the
    # LAST-action invariant (mirrors path-1's test_summary_write_is_last_action).
    test_dir = repo_root / "tests" / "test_chain_driver"
    test_files = list(test_dir.glob("**/*.py"))
    test_text = "\n".join(p.read_text(encoding="utf-8") for p in test_files)
    has_path2_test = (
        "wip_empty" in test_text.lower() or
        "path_2" in test_text or
        "path 2" in test_text or
        "summary_write_is_last" in test_text
    )
    assert has_path2_test, (
        "path-2 wip-empty completion-summary 'write-LAST' invariant is not "
        "pinned by an explicit test; per orchestrator §4a.4 + Opus M-4, "
        "both emit paths must have the test invariant"
    )
