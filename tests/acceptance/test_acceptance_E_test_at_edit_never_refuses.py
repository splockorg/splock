"""E.19 — `test-at-edit.sh` PostToolUse always exits 0 + logs on the "ran" path.

Per inventory + userguide §5.3 "Never refuses (PostToolUse contract)" +
implplan §M.impl.6 "Verification-only contract per R-POSTTOOL-NO-DENY:
always exits 0".

Two assertions:
- (a) Shell hook against an edit event always returns exit 0 with no
  `permissionDecision: deny` in stdout — even when the path triggers
  pytest and the mirrored test FAILS. The hook is observational; it
  must surface the failure via the log, not by refusing the edit.
- (b) Python backing `process_event(...)` on the "ran" path appends a
  row to `.claude/state/test_at_edit_log.jsonl` containing the
  per-invocation result (tests_run, failing, duration_seconds, src).

Combines both layers so a regression in either side is caught
deterministically without spinning up real pytest.
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path


pytestmark = pytest.mark.acceptance


def _has_deny_in(stdout: str) -> bool:
    if not stdout.strip():
        return False
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return False
    if isinstance(payload, dict) and payload.get("permissionDecision") == "deny":
        return True
    hso = payload.get("hookSpecificOutput") if isinstance(payload, dict) else None
    return isinstance(hso, dict) and hso.get("permissionDecision") == "deny"


def test_shell_hook_exits_zero_on_skipped_path(
    repo_root, hook_event_injector, posttool_use_event
):
    """E.19a: skipped-path edit (under docs/) — hook returns 0, never refuses."""
    hook = repo_root / "hooks" / "test-at-edit.sh"
    assert hook.exists()
    event = posttool_use_event(
        tool="Edit",
        tool_input={
            "file_path": "docs/foo/notes.md",
            "old_string": "x",
            "new_string": "y",
        },
        tool_response={"success": True},
        cwd=str(repo_root),
    )
    result = hook_event_injector(hook, event)
    assert result.returncode == 0, (
        f"test-at-edit.sh PostToolUse must always exit 0 (R-POSTTOOL-NO-DENY); "
        f"got {result.returncode}; stderr: {result.stderr!r}"
    )
    assert not _has_deny_in(result.stdout), (
        "PostToolUse hook emitted permissionDecision=deny — violates "
        f"R-POSTTOOL-NO-DENY contract. stdout={result.stdout!r}"
    )


def test_shell_hook_exits_zero_on_no_matching_tests(
    repo_root, hook_event_injector, posttool_use_event
):
    """E.19b: source-file edit that doesn't match any tests still exits 0."""
    hook = repo_root / "hooks" / "test-at-edit.sh"
    event = posttool_use_event(
        tool="Edit",
        tool_input={
            "file_path": "bin/__nonexistent_no_test_match__.py",
            "old_string": "x",
            "new_string": "y",
        },
        tool_response={"success": True},
        cwd=str(repo_root),
    )
    result = hook_event_injector(hook, event)
    assert result.returncode == 0
    assert not _has_deny_in(result.stdout)


def test_python_backing_logs_row_on_ran_path_with_failing_tests(tmp_path, monkeypatch):
    """E.19c: process_event on the "ran" path always appends a log row,
    even when the mirrored test fails (substrate's failure surface = the log).
    """
    from bin._hooks import test_at_edit

    # Build a tmp repo with one source file + one mirrored test that fails.
    repo = tmp_path / "tmp_repo"
    (repo / "bin").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    src = repo / "bin" / "widget.py"
    src.write_text("def widget(): return 1\n", encoding="utf-8")
    mirror = repo / "tests" / "test_widget.py"
    mirror.write_text(
        "def test_always_fails():\n    assert False\n",
        encoding="utf-8",
    )

    # Stub the test-discovery helper to return our mirror file deterministically.
    monkeypatch.setattr(
        "bin._hooks.test_at_edit.find_matching_tests",
        lambda src_path, **kw: [mirror],
    )

    # Stub _run_pytest so we don't actually spawn pytest; assert this is the
    # path that produces a "ran" action + logged row.
    def _fake_pytest(test_files, *, repo_root, **kwargs):
        return {
            "tests_run": len(test_files),
            "failing": len(test_files),  # all failing
            "skipped_timeout": False,
            "duration_seconds": 0.01,
        }
    monkeypatch.setattr("bin._hooks.test_at_edit._run_pytest", _fake_pytest)

    event = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": "bin/widget.py"},
        "tool_response": {"success": True},
    }
    result = test_at_edit.process_event(event, repo_root=repo)

    assert result["action"] == "ran", (
        f"expected action=ran for source-file edit with matching test; got {result!r}"
    )
    row = result["row"]
    assert row["failing"] == 1, "expected failing count to surface in row"
    assert row["tests_run"] == 1

    # Verify log file written + row appended.
    log_path = repo / test_at_edit.LOG_FILENAME
    assert log_path.exists(), f"log file not written at {log_path}"
    lines = [l for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1, f"expected 1 log row; got {len(lines)}"
    logged = json.loads(lines[0])
    assert logged["src"] == "bin/widget.py"
    assert logged["failing"] == 1
    assert "ts" in logged


def test_python_backing_logs_row_on_ran_path_with_passing_tests(tmp_path, monkeypatch):
    """E.19d: happy-path edit (test passes) still appends a log row."""
    from bin._hooks import test_at_edit

    repo = tmp_path / "tmp_repo2"
    (repo / "bin").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / "bin" / "widget2.py").write_text("def w(): return 1\n", encoding="utf-8")
    mirror = repo / "tests" / "test_widget2.py"
    mirror.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    monkeypatch.setattr(
        "bin._hooks.test_at_edit.find_matching_tests",
        lambda src_path, **kw: [mirror],
    )

    def _fake_pytest_ok(test_files, *, repo_root, **kwargs):
        return {
            "tests_run": len(test_files),
            "failing": 0,
            "skipped_timeout": False,
            "duration_seconds": 0.01,
        }
    monkeypatch.setattr("bin._hooks.test_at_edit._run_pytest", _fake_pytest_ok)

    event = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "bin/widget2.py"},
    }
    result = test_at_edit.process_event(event, repo_root=repo)
    assert result["action"] == "ran"
    assert result["row"]["failing"] == 0

    log_path = repo / test_at_edit.LOG_FILENAME
    assert log_path.exists()
    lines = [l for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["failing"] == 0
