"""The retry loop's `tests_enabled` pre-flight: selector classification.

Before the loop hands anything to pytest it partitions each `tests_enabled`
entry into *runnable selectors* and *skipped* entries. Prose ("CLI-version
doc") and node-IDs for files nobody has authored yet both make pytest exit at
collection, which burns the retry budget on an unfixable invocation. The
pre-flight catches both cheaply.

Also pinned here — and NOT covered upstream — is that `_repo_root()` resolves
the **adopter's** repo, not the plugin's. Upstream walks `parents[2]` off
`__file__`, which under an installed plugin is the plugin cache: the on-disk
selector check would then test for the adopter's files inside the plugin tree
and classify every real selector as skipped. That is fork finding F3.

`run_typed_gate_command` is exercised too. It is deliberately unwired — no gate
verdict path calls it — but it is a shipped, reserved surface, so its argument
contract is pinned rather than left to rot.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bin._retry_loop.sdk_spawners import (
    COLLECT_TYPED_COMMAND,
    _repo_root,
    is_runnable_pytest_selector,
    partition_runnable_selectors,
    read_tests_enabled_union,
    run_typed_gate_command,
)
from bin._verify_plan.strict import TYPED_GATE_COMMAND_PREFIX


# --------------------------------------------------------------------------- #
# F3: the adopter's root, not the plugin's                                     #
# --------------------------------------------------------------------------- #


def test_repo_root_follows_the_adopter_project(tmp_path, monkeypatch):
    """`_repo_root()` must track $CLAUDE_PROJECT_DIR, not this file's parents.

    A `parents[2]` walk would return the plugin tree under an installed
    plugin, so every adopter selector would fail the on-disk check.
    """
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert _repo_root() == tmp_path.resolve()


def test_selector_check_resolves_against_the_adopter_root(tmp_path, monkeypatch):
    """The on-disk check follows `_repo_root()`, so an adopter-only test file
    is runnable even though it does not exist under the plugin tree."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_adopter_only.py").write_text("", encoding="utf-8")

    assert is_runnable_pytest_selector("tests/test_adopter_only.py::test_x")
    # ...and it is NOT resolvable against the plugin checkout.
    plugin_tree = Path(__file__).resolve().parents[2]
    assert not (plugin_tree / "tests" / "test_adopter_only.py").exists()


# --------------------------------------------------------------------------- #
# is_runnable_pytest_selector                                                  #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_real.py").write_text("", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "selector",
    [
        "tests/test_real.py",
        "tests/test_real.py::test_thing",
        "tests",  # a directory is a legal pytest target
        "tests/test_real.py::test_p[a b c]",  # spaces inside [...] are fine
    ],
)
def test_runnable_selectors_accepted(selector: str, repo: Path) -> None:
    assert is_runnable_pytest_selector(selector, repo)


@pytest.mark.parametrize(
    "entry",
    [
        "CLI-version doc",  # prose: whitespace in the path component
        "claude plugin validate . clean",  # prose that looks command-ish
        "tests/test_not_authored_yet.py::test_x",  # syntactically fine, absent
        "",  # empty
        "   ",  # whitespace only
        "::test_x",  # empty path component
    ],
)
def test_non_runnable_entries_rejected(entry: str, repo: Path) -> None:
    assert not is_runnable_pytest_selector(entry, repo)


# --------------------------------------------------------------------------- #
# partition_runnable_selectors                                                 #
# --------------------------------------------------------------------------- #


def test_partition_splits_and_preserves_input_order(repo: Path) -> None:
    entries = [
        "tests/test_real.py::test_a",
        "some design prose",
        "tests/test_real.py::test_b",
        "tests/test_absent.py",
    ]
    runnable, skipped = partition_runnable_selectors(entries, repo)
    assert runnable == ["tests/test_real.py::test_a", "tests/test_real.py::test_b"]
    assert skipped == ["some design prose", "tests/test_absent.py"]


def test_partition_of_empty_input_is_two_empty_lists(repo: Path) -> None:
    assert partition_runnable_selectors([], repo) == ([], [])


# --------------------------------------------------------------------------- #
# read_tests_enabled_union                                                     #
# --------------------------------------------------------------------------- #


def test_union_dedupes_sorts_and_ignores_non_strings(tmp_path: Path) -> None:
    orch = tmp_path / "o.json"
    orch.write_text(
        '{"tasks": ['
        '  {"tests_enabled": ["b::t", "a::t", "b::t"]},'
        '  {"tests_enabled": ["a::t", "", null, 7]},'
        '  {"tests_enabled": []},'
        '  {}'
        "]}",
        encoding="utf-8",
    )
    assert read_tests_enabled_union(orch) == ["a::t", "b::t"]


# --------------------------------------------------------------------------- #
# run_typed_gate_command — reserved, unwired, but pinned                        #
# --------------------------------------------------------------------------- #


def test_typed_gate_prefix_constant_is_the_classification_name() -> None:
    assert COLLECT_TYPED_COMMAND == "typed_gate_command"


def test_typed_gate_rejects_entry_without_the_prefix(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a typed gate command"):
        run_typed_gate_command("pytest -q", cwd=tmp_path)


def test_typed_gate_rejects_empty_command(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        run_typed_gate_command(f"{TYPED_GATE_COMMAND_PREFIX}   ", cwd=tmp_path)


def test_typed_gate_returncode_is_not_coerced(tmp_path: Path) -> None:
    """Exit 0 is pass; every other code surfaces intact (the Aider model)."""
    entry = f'{TYPED_GATE_COMMAND_PREFIX}{sys.executable} -c "import sys; sys.exit(3)"'
    result = run_typed_gate_command(entry, cwd=tmp_path)
    assert isinstance(result, subprocess.CompletedProcess)
    assert result.returncode == 3


def test_typed_gate_passes_on_exit_zero_and_captures_stdout(tmp_path: Path) -> None:
    entry = f'{TYPED_GATE_COMMAND_PREFIX}{sys.executable} -c "print(\'gate ok\')"'
    result = run_typed_gate_command(entry, cwd=tmp_path)
    assert result.returncode == 0
    assert "gate ok" in result.stdout


def test_typed_gate_runs_without_a_shell(tmp_path: Path) -> None:
    """Split via shlex, never `shell=True` — so shell metacharacters are inert.

    If this ran through a shell the `&& touch pwned` would execute; it must
    instead be passed as literal argv to python, which ignores it.
    """
    sentinel = tmp_path / "pwned"
    entry = (
        f"{TYPED_GATE_COMMAND_PREFIX}{sys.executable} -c pass "
        f"&& touch {sentinel}"
    )
    result = run_typed_gate_command(entry, cwd=tmp_path)
    assert not sentinel.exists()
    # python -c pass with extra argv entries still exits 0.
    assert result.returncode == 0
