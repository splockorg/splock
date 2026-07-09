"""The briefing's working-tree diff section.

The reviewer briefing has always carried an *iteration* diff (what the last
spawn changed). It now also carries a **working-tree** diff: everything
uncommitted at briefing time, tracked and untracked.

Two properties are load-bearing:

1. **A clean tree renders an explicit sentinel, never an empty section.** An
   absent section is ambiguous — the reviewer cannot tell "no uncommitted
   edits" apart from "the capture failed". The sentinel removes that ambiguity.
2. **Untracked files are reported by path.** `git diff` cannot content-diff a
   file git has never seen, so a fabricated-but-uncommitted test file would be
   invisible to a diff-only capture. `git status --porcelain` `??` entries close
   that hole — which matters, because that is exactly the shape of the
   fabrication the repair write-scope guard exists to catch.

Sections are byte-capped independently, so one enormous diff cannot crowd the
other out of the reviewer's context window.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bin._retry_loop.briefing import (
    DIFF_SECTION_MAX_BYTES,
    WORKING_TREE_CLEAN_SENTINEL,
    WORKING_TREE_DIFF_HEADING,
    _cap_diff_section,
    _compute_working_tree_diff,
    _git_cwd,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


# --------------------------------------------------------------------------- #
# _git_cwd                                                                      #
# --------------------------------------------------------------------------- #


def test_git_cwd_uses_the_plan_dir_when_it_carries_dot_git(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    assert _git_cwd(tmp_path) == str(tmp_path)


def test_git_cwd_falls_back_to_the_parent(tmp_path: Path) -> None:
    """git resolves the enclosing repo from any subdirectory."""
    plan_dir = tmp_path / "docs" / "plans" / "slug"
    plan_dir.mkdir(parents=True)
    assert _git_cwd(plan_dir) == str(plan_dir.parent)


# --------------------------------------------------------------------------- #
# clean vs dirty                                                                #
# --------------------------------------------------------------------------- #


def test_clean_tree_renders_the_explicit_sentinel(repo: Path) -> None:
    """Never an empty section: "clean" and "capture failed" must be tellable apart."""
    assert _compute_working_tree_diff(repo) == WORKING_TREE_CLEAN_SENTINEL


def test_modified_tracked_file_appears_in_the_diff(repo: Path) -> None:
    (repo / "tracked.py").write_text("x = 2\n", encoding="utf-8")
    out = _compute_working_tree_diff(repo)
    assert out != WORKING_TREE_CLEAN_SENTINEL
    assert "tracked.py" in out
    assert "-x = 1" in out and "+x = 2" in out


def test_staged_but_uncommitted_change_appears(repo: Path) -> None:
    """`git diff HEAD` covers staged changes too, not just unstaged ones."""
    (repo / "tracked.py").write_text("x = 3\n", encoding="utf-8")
    _git(repo, "add", "tracked.py")
    assert "+x = 3" in _compute_working_tree_diff(repo)


def test_untracked_file_in_a_tracked_dir_is_named(repo: Path) -> None:
    """`git diff` cannot see a file git has never seen.

    A fabricated-but-uncommitted test file would otherwise be invisible to the
    reviewer — the exact shape the repair write-scope guard exists to catch.
    """
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_existing.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add tests")

    (tests / "test_fabricated.py").write_text(
        "def test_free_green():\n    assert True\n", encoding="utf-8"
    )

    out = _compute_working_tree_diff(repo)

    assert out != WORKING_TREE_CLEAN_SENTINEL
    assert "tests/test_fabricated.py" in out


def test_untracked_directory_collapses_to_the_directory_path(repo: Path) -> None:
    """A granularity limit inherited from `git status --porcelain`.

    When the containing directory is itself untracked, porcelain reports the
    DIRECTORY (`?? tests/`) rather than each file under it. The reviewer still
    sees that something new appeared, but not which files — so a briefing alone
    cannot enumerate fabrications inside a brand-new directory. That is why the
    repair write-scope guard walks the tree itself rather than parsing this.
    """
    (repo / "tests").mkdir()
    (repo / "tests" / "test_fabricated.py").write_text("x = 1\n", encoding="utf-8")

    out = _compute_working_tree_diff(repo)

    assert out != WORKING_TREE_CLEAN_SENTINEL
    assert "tests/" in out
    assert "test_fabricated.py" not in out


def test_untracked_and_modified_are_reported_together(repo: Path) -> None:
    (repo / "tracked.py").write_text("x = 9\n", encoding="utf-8")
    (repo / "new.py").write_text("y = 1\n", encoding="utf-8")
    out = _compute_working_tree_diff(repo)
    assert "+x = 9" in out
    assert "new.py" in out


def test_not_a_git_repo_does_not_raise(tmp_path: Path) -> None:
    """Capture failures degrade to a sentinel; the briefing must still build."""
    out = _compute_working_tree_diff(tmp_path)
    assert isinstance(out, str) and out


# --------------------------------------------------------------------------- #
# per-section byte cap                                                          #
# --------------------------------------------------------------------------- #


def test_short_section_is_passed_through_unchanged() -> None:
    assert _cap_diff_section("small") == "small"


def test_oversized_section_is_truncated_with_a_sentinel() -> None:
    raw = "a" * (DIFF_SECTION_MAX_BYTES + 500)
    capped = _cap_diff_section(raw)
    assert len(capped) < len(raw)
    assert capped.startswith("a" * 100)
    assert capped != raw and not capped.endswith("a")


def test_truncation_survives_a_multibyte_codepoint_cut() -> None:
    """A cut mid-codepoint must not raise — re-decode with errors='replace'."""
    raw = "é" * DIFF_SECTION_MAX_BYTES  # 2 bytes each, so the cap lands mid-char
    capped = _cap_diff_section(raw)
    assert isinstance(capped, str)


def test_heading_is_distinct_from_the_iteration_diff_heading() -> None:
    from bin._retry_loop.briefing import ITERATION_DIFF_HEADING

    assert WORKING_TREE_DIFF_HEADING != ITERATION_DIFF_HEADING
    assert "uncommitted" in WORKING_TREE_DIFF_HEADING.lower()
