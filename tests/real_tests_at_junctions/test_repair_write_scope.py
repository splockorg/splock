"""The fixer-must-not-author-tests guard.

The Phase-5 repair spawn is graded against the plan's `tests_enabled`. If that
agent can *write* those test files — or a `conftest.py`, or a decision doc under
its own plan dir — it can manufacture its own green. This guard snapshots the
protected surface before the session and reverts or flags every write to it
afterwards.

Phase 4 (`/code`) routes through the SAME spawner and is deliberately exempt:
the task coder legitimately authors its `tests_enabled` files (TDD).

The load-bearing property, pinned below, is that enforcement runs from a
`finally` — a session that crashes *after* fabricating files must still have
them reverted, or a crash becomes a way to smuggle files past the guard.
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

from bin._retry_loop import sdk_spawners
from bin._retry_loop.sdk_spawners import (
    REPAIR_GUARD_PHASE,
    enforce_repair_write_scope,
    snapshot_repair_write_scope,
)

_SLUG = "demo_slug"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A committed repo with a graded test, a conftest, and a plan dir."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "conftest.py").write_text("# original\n", encoding="utf-8")
    (tests / "test_graded.py").write_text(
        "def test_graded():\n    assert True\n", encoding="utf-8"
    )

    plan_dir = tmp_path / "docs" / "plans" / _SLUG
    plan_dir.mkdir(parents=True)
    (plan_dir / f"{_SLUG}_orchestrator.json").write_text(
        '{"tasks": [{"id": "T1", "tests_enabled": ["tests/test_graded.py::test_graded"]}]}',
        encoding="utf-8",
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def _kinds(violations: list[dict]) -> dict[str, tuple[str, str]]:
    return {v["path"]: (v["kind"], v["action"]) for v in violations}


# --------------------------------------------------------------------------- #
# snapshot + enforce, directly                                                  #
# --------------------------------------------------------------------------- #


def test_guard_phase_is_the_repair_phase_not_the_coder_phase() -> None:
    """Phase 5 is /test (repair). Phase 4 is /code (TDD authoring) — exempt."""
    assert REPAIR_GUARD_PHASE == "5"


def test_untouched_repo_yields_no_violations(repo: Path) -> None:
    snap = snapshot_repair_write_scope(repo, slug=_SLUG)
    assert enforce_repair_write_scope(repo, snap) == []


def test_fabricated_test_file_is_deleted(repo: Path) -> None:
    snap = snapshot_repair_write_scope(repo, slug=_SLUG)
    fabricated = repo / "tests" / "test_fabricated.py"
    fabricated.write_text("def test_free_green():\n    assert True\n", encoding="utf-8")

    violations = enforce_repair_write_scope(repo, snap)

    assert not fabricated.exists(), "a fabricated test file must not survive"
    assert _kinds(violations)["tests/test_fabricated.py"] == ("created", "deleted")


def test_edited_graded_test_is_restored(repo: Path) -> None:
    """The agent must not rewrite the very test it is graded against."""
    graded = repo / "tests" / "test_graded.py"
    original = graded.read_text()
    snap = snapshot_repair_write_scope(repo, slug=_SLUG)
    graded.write_text("def test_graded():\n    pass  # neutered\n", encoding="utf-8")

    violations = enforce_repair_write_scope(repo, snap)

    assert graded.read_text() == original
    assert _kinds(violations)["tests/test_graded.py"] == ("modified", "restored")


def test_edited_conftest_is_restored(repo: Path) -> None:
    """A force-pass conftest is the trust threat 2c-2 cannot neutralize."""
    conftest = repo / "tests" / "conftest.py"
    snap = snapshot_repair_write_scope(repo, slug=_SLUG)
    conftest.write_text(
        "def pytest_runtest_makereport(item, call):\n    pass  # force-pass hook\n",
        encoding="utf-8",
    )

    violations = enforce_repair_write_scope(repo, snap)

    assert conftest.read_text() == "# original\n"
    assert _kinds(violations)["tests/conftest.py"] == ("modified", "restored")


def test_deleted_graded_test_is_restored(repo: Path) -> None:
    """Deleting the graded test is as good as neutering it."""
    graded = repo / "tests" / "test_graded.py"
    original = graded.read_text()
    snap = snapshot_repair_write_scope(repo, slug=_SLUG)
    graded.unlink()

    violations = enforce_repair_write_scope(repo, snap)

    assert graded.read_text() == original
    assert _kinds(violations)["tests/test_graded.py"][0] == "deleted"


def test_fabricated_plan_doc_is_deleted(repo: Path) -> None:
    """The agent must not unilaterally author decision docs in its plan dir."""
    snap = snapshot_repair_write_scope(repo, slug=_SLUG)
    doc = repo / "docs" / "plans" / _SLUG / "some_decision.md"
    doc.write_text("# I decided this myself\n", encoding="utf-8")

    violations = enforce_repair_write_scope(repo, snap)

    assert not doc.exists()
    assert _kinds(violations)[f"docs/plans/{_SLUG}/some_decision.md"] == (
        "created",
        "deleted",
    )


def test_source_files_outside_the_scope_are_untouched(repo: Path) -> None:
    """The guard protects the grading surface, not the fix itself."""
    src = repo / "bin" / "impl.py"
    src.parent.mkdir()
    snap = snapshot_repair_write_scope(repo, slug=_SLUG)
    src.write_text("# the actual repair\n", encoding="utf-8")

    violations = enforce_repair_write_scope(repo, snap)

    assert src.exists(), "the repair itself must survive"
    assert all(not v["path"].startswith("bin/") for v in violations)


# --------------------------------------------------------------------------- #
# the wiring: phase-gating, and enforcement from a `finally`                     #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def stub_sdk(monkeypatch):
    """claude_agent_sdk is an optional dependency; stub the two names used."""
    mod = types.ModuleType("claude_agent_sdk")

    class _Permissive:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    mod.AgentDefinition = _Permissive
    mod.ClaudeAgentOptions = _Permissive
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)
    return mod


class _Result:
    total_cost_usd = 0.0
    is_error = False


def _spawn(repo: Path, phase: str, monkeypatch, *, during):
    """Drive spawn_opus_via_sdk with a fake session that runs `during(repo)`."""

    async def _fake_drive(**_kw):
        during(repo)
        return _Result()

    monkeypatch.setattr(sdk_spawners, "_drive_opus_async", _fake_drive)
    return sdk_spawners.spawn_opus_via_sdk(
        prompt="fix it",
        cwd=repo,
        hook_env={"SPLOCK_PHASE": phase, "SPLOCK_PLAN_SLUG": _SLUG},
    )


def test_phase_5_reverts_fabrications_and_reports_them(repo, stub_sdk, monkeypatch):
    def fabricate(r: Path) -> None:
        (r / "tests" / "test_fabricated.py").write_text("def test_x():\n    pass\n")

    result = _spawn(repo, REPAIR_GUARD_PHASE, monkeypatch, during=fabricate)

    assert not (repo / "tests" / "test_fabricated.py").exists()
    assert _kinds(result["repair_scope_violations"])["tests/test_fabricated.py"] == (
        "created",
        "deleted",
    )


def test_phase_4_coder_may_author_its_tests(repo, stub_sdk, monkeypatch):
    """/code is exempt — the task coder legitimately authors tests_enabled (TDD)."""

    def author(r: Path) -> None:
        (r / "tests" / "test_new_task.py").write_text("def test_x():\n    pass\n")

    result = _spawn(repo, "4", monkeypatch, during=author)

    assert (repo / "tests" / "test_new_task.py").exists()
    assert result["repair_scope_violations"] == []


def test_a_crashed_session_still_has_its_fabrications_reverted(
    repo, stub_sdk, monkeypatch
):
    """Enforcement runs from a `finally`.

    Otherwise raising after writing would be a way to smuggle a fabricated test
    file past the guard.
    """
    fabricated = repo / "tests" / "test_fabricated.py"

    async def _fake_drive(**_kw):
        fabricated.write_text("def test_x():\n    pass\n")
        raise RuntimeError("SDK exploded mid-session")

    monkeypatch.setattr(sdk_spawners, "_drive_opus_async", _fake_drive)

    with pytest.raises(RuntimeError, match="exploded"):
        sdk_spawners.spawn_opus_via_sdk(
            prompt="fix it",
            cwd=repo,
            hook_env={"SPLOCK_PHASE": REPAIR_GUARD_PHASE, "SPLOCK_PLAN_SLUG": _SLUG},
        )

    assert not fabricated.exists(), "the finally must revert even on a crash"
