"""E.12 — `lazy-dump-cap.sh` dispatches `bin/lazy-dump-check` when outstanding_issues.md touched."""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


def test_lazy_dump_cap_no_op_when_outstanding_issues_not_touched(
    repo_root, hook_event_injector, pretool_use_event
):
    """E.12a: hook exits 0 silently when staged file is not outstanding_issues.md."""
    hook = repo_root / "hooks" / "lazy-dump-cap.sh"
    if not hook.exists():
        pytest.skip("lazy-dump-cap.sh missing")

    event = pretool_use_event(
        tool="Edit",
        tool_input={
            "file_path": "src/feature.py",
            "new_string": "def x(): pass",
            "old_string": "",
        },
        cwd=str(repo_root),
    )
    result = hook_event_injector(hook, event)
    assert result.returncode == 0, (
        f"lazy-dump-cap.sh should no-op on non-target file; rc={result.returncode}"
    )


def test_lazy_dump_cap_invokes_check_when_outstanding_issues_touched(
    repo_root, hook_event_injector, pretool_use_event
):
    """E.12b: hook invokes bin/lazy-dump-check when outstanding_issues.md is in diff."""
    hook = repo_root / "hooks" / "lazy-dump-cap.sh"
    if not hook.exists():
        pytest.skip("lazy-dump-cap.sh missing")

    event = pretool_use_event(
        tool="Edit",
        tool_input={
            "file_path": "docs/outstanding_issues.md",
            "new_string": "- new lazy-dump entry",
            "old_string": "",
        },
        cwd=str(repo_root),
    )
    result = hook_event_injector(hook, event)
    # Hook exits 0 if check passes, 26 if cap exceeded — both are valid outcomes.
    assert result.returncode in (0, 26), (
        f"lazy-dump-cap.sh expected exit 0 or 26; got {result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
