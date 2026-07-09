"""Hardening of the retry loop's pytest invocation, and the trust check on green.

Three properties, all of which a naive `subprocess.run(["pytest", ...])` lacks:

1. **argv is hardened and adopter-correct.** `build_pytest_argv` clamps
   `addopts=` and disables the cache plugin, and `argv[0]` is the ADOPTER's
   python — not the plugin venv's `sys.executable`. Upstream hardcodes
   `sys.executable`; under an installed plugin that interpreter cannot import
   the adopter's test dependencies, so every graded run would fail for the
   wrong reason. That is fork finding F3, and the assertions below are
   deliberately divergent from upstream's (which pin `sys.executable`).

2. **The env channel is clamped.** `PYTEST_ADDOPTS` can inject argv (`--co`
   turns every run collect-only and exits 0 without executing a test — a
   silent false-green) and `PYTEST_PLUGINS` imports arbitrary code. Neither is
   reachable from argv flags, so `pytest_subprocess_env` strips exactly those
   two and nothing else.

3. **Green is not automatically trusted.** A `conftest.py` can force-pass
   failures in-process (`pytest_runtest_makereport` flipping failed→passed);
   no flag set can prevent it. So a green run whose conftest/ini trust surface
   is untracked-or-modified relative to git HEAD is coerced to
   `UNTRUSTED_GREEN_RETURNCODE`. Red results are never touched — a real failure
   must reach the retry loop intact.

Note on F3's other half: `run_verify_subprocess` used to pass selectors via a
`-k "a or b"` expression, because `tests_enabled` held bare node names. Cluster
2a's validator now requires path-bound node IDs (`tests/x.py::test_y`), which a
`-k` expression cannot express (no `/`, no `::`). Selectors are therefore passed
positionally, as pytest node IDs are meant to be.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bin._retry_loop import sdk_spawners
from bin._retry_loop.sdk_spawners import (
    PYTEST_HARDENING_FLAGS,
    UNTRUSTED_GREEN_RETURNCODE,
    _test_interpreter,
    build_pytest_argv,
    pytest_subprocess_env,
    pytest_trust_surface,
    run_verify_subprocess,
    untrusted_pytest_trust_surface,
)


# --------------------------------------------------------------------------- #
# helpers                                                                       #
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """A committed git repo with one passing test and a conftest."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "conftest.py").write_text("", encoding="utf-8")
    (tests / "test_green.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def _orchestrator(plan_dir: Path, tests_enabled: list[str]) -> Path:
    plan_dir.mkdir(parents=True, exist_ok=True)
    path = plan_dir / "o.json"
    path.write_text(
        json.dumps({"tasks": [{"id": "T1", "tests_enabled": tests_enabled}]}),
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# 1. argv shape + F3: the adopter's interpreter                                 #
# --------------------------------------------------------------------------- #


def test_hardening_flags_clamp_addopts_and_cache() -> None:
    assert PYTEST_HARDENING_FLAGS == ("-p", "no:cacheprovider", "--override-ini", "addopts=")


def test_argv_is_interpreter_dash_m_pytest_then_flags_then_selectors() -> None:
    argv = build_pytest_argv(["tests/test_a.py::test_x", "tests/test_b.py"])
    assert argv[0] == _test_interpreter()
    assert argv[1:3] == ["-m", "pytest"]
    assert argv[3 : 3 + len(PYTEST_HARDENING_FLAGS)] == list(PYTEST_HARDENING_FLAGS)
    assert argv[-3:] == ["tests/test_a.py::test_x", "tests/test_b.py", "-v"]


def test_selectors_are_positional_not_a_dash_k_expression() -> None:
    """F3's `-k` half is superseded: `-k` cannot express `path::nodeid`."""
    argv = build_pytest_argv(["tests/test_a.py::test_x"])
    assert "-k" not in argv
    assert "tests/test_a.py::test_x" in argv


def test_interpreter_prefers_the_explicit_override(monkeypatch) -> None:
    monkeypatch.setenv("SPLOCK_TEST_PYTHON", "/custom/python")
    assert _test_interpreter() == "/custom/python"


def test_interpreter_prefers_the_adopter_venv_over_sys_executable(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("SPLOCK_TEST_PYTHON", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    venv_py = tmp_path / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("", encoding="utf-8")
    assert _test_interpreter() == str(venv_py)


def test_interpreter_falls_back_to_sys_executable_in_tree(tmp_path, monkeypatch) -> None:
    """Sideloaded / in-tree mode: no adopter .venv, so the running python."""
    monkeypatch.delenv("SPLOCK_TEST_PYTHON", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert _test_interpreter() == sys.executable


# --------------------------------------------------------------------------- #
# 2. the env-channel clamp                                                      #
# --------------------------------------------------------------------------- #


def test_env_strips_exactly_the_two_injection_channels(monkeypatch) -> None:
    monkeypatch.setenv("PYTEST_ADDOPTS", "--co")
    monkeypatch.setenv("PYTEST_PLUGINS", "evil_plugin")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.setenv("PATH_MARKER_FOR_TEST", "keep-me")

    env = pytest_subprocess_env()

    assert "PYTEST_ADDOPTS" not in env
    assert "PYTEST_PLUGINS" not in env
    # Every other PYTEST_* var passes through: stripping autoload-disable
    # would change collection behaviour.
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert env["PATH_MARKER_FOR_TEST"] == "keep-me"


def test_env_does_not_mutate_os_environ(monkeypatch) -> None:
    monkeypatch.setenv("PYTEST_ADDOPTS", "--co")
    pytest_subprocess_env()
    assert os.environ["PYTEST_ADDOPTS"] == "--co"


# --------------------------------------------------------------------------- #
# 3. the trust surface                                                          #
# --------------------------------------------------------------------------- #


def test_trust_surface_collects_existing_conftest_on_the_chain(git_repo: Path) -> None:
    surface = pytest_trust_surface(["tests/test_green.py::test_ok"], git_repo)
    assert "tests/conftest.py" in surface


def test_committed_clean_surface_is_trusted(git_repo: Path) -> None:
    assert untrusted_pytest_trust_surface(["tests/test_green.py::test_ok"], git_repo) == []


def test_modified_conftest_is_untrusted(git_repo: Path) -> None:
    (git_repo / "tests" / "conftest.py").write_text("# touched\n", encoding="utf-8")
    untrusted = untrusted_pytest_trust_surface(["tests/test_green.py::test_ok"], git_repo)
    assert "tests/conftest.py" in untrusted


def test_untracked_conftest_is_untrusted(git_repo: Path) -> None:
    nested = git_repo / "tests" / "nested"
    nested.mkdir()
    (nested / "conftest.py").write_text("", encoding="utf-8")
    (nested / "test_n.py").write_text("def test_n():\n    assert True\n", encoding="utf-8")
    untrusted = untrusted_pytest_trust_surface(["tests/nested/test_n.py"], git_repo)
    assert "tests/nested/conftest.py" in untrusted


# --------------------------------------------------------------------------- #
# 4. run_verify_subprocess end-to-end                                           #
# --------------------------------------------------------------------------- #


def test_empty_tests_enabled_raises(tmp_path: Path) -> None:
    plan_dir = tmp_path / "plan"
    orch = _orchestrator(plan_dir, [])
    with pytest.raises(ValueError, match="no tests_enabled"):
        run_verify_subprocess(
            slug="s", plan_dir=plan_dir, orchestrator_path=orch, iteration_n=1
        )


def test_zero_runnable_selectors_returns_synthetic_exit_4_without_spawning(
    tmp_path: Path, monkeypatch
) -> None:
    """A bare pytest with no test-ids would collect the ENTIRE repo suite."""
    monkeypatch.setattr(sdk_spawners, "_repo_root", lambda: tmp_path)

    def _boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("subprocess.run must not be called")

    monkeypatch.setattr(sdk_spawners.subprocess, "run", _boom)

    plan_dir = tmp_path / "plan"
    orch = _orchestrator(plan_dir, ["some design prose", "tests/absent.py::t"])
    result = run_verify_subprocess(
        slug="s", plan_dir=plan_dir, orchestrator_path=orch, iteration_n=1
    )
    assert result.returncode == 4
    assert "no runnable pytest selectors" in result.stderr


def test_green_run_on_a_clean_repo_is_trusted(git_repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(sdk_spawners, "_repo_root", lambda: git_repo)
    monkeypatch.setenv("SPLOCK_TEST_PYTHON", sys.executable)
    plan_dir = git_repo / "plan"
    orch = _orchestrator(plan_dir, ["tests/test_green.py::test_ok"])

    result = run_verify_subprocess(
        slug="s", plan_dir=plan_dir, orchestrator_path=orch, iteration_n=1
    )
    assert result.returncode == 0


def test_green_run_with_a_dirty_conftest_is_coerced_to_untrusted(
    git_repo: Path, monkeypatch
) -> None:
    monkeypatch.setattr(sdk_spawners, "_repo_root", lambda: git_repo)
    monkeypatch.setenv("SPLOCK_TEST_PYTHON", sys.executable)
    # A force-pass conftest is the threat; merely dirtying it is enough to
    # withdraw trust, because the loop cannot know what the edit did.
    (git_repo / "tests" / "conftest.py").write_text("# edited\n", encoding="utf-8")

    plan_dir = git_repo / "plan"
    orch = _orchestrator(plan_dir, ["tests/test_green.py::test_ok"])
    result = run_verify_subprocess(
        slug="s", plan_dir=plan_dir, orchestrator_path=orch, iteration_n=1
    )
    assert result.returncode == UNTRUSTED_GREEN_RETURNCODE
    assert "UNTRUSTED-GREEN" in result.stderr
    assert "tests/conftest.py" in result.stderr


def test_red_run_is_never_coerced_even_with_a_dirty_conftest(
    git_repo: Path, monkeypatch
) -> None:
    """Trust is checked on GREEN only; a real failure must arrive intact."""
    monkeypatch.setattr(sdk_spawners, "_repo_root", lambda: git_repo)
    monkeypatch.setenv("SPLOCK_TEST_PYTHON", sys.executable)
    (git_repo / "tests" / "test_red.py").write_text(
        "def test_bad():\n    assert False\n", encoding="utf-8"
    )
    (git_repo / "tests" / "conftest.py").write_text("# edited\n", encoding="utf-8")

    plan_dir = git_repo / "plan"
    orch = _orchestrator(plan_dir, ["tests/test_red.py::test_bad"])
    result = run_verify_subprocess(
        slug="s", plan_dir=plan_dir, orchestrator_path=orch, iteration_n=1
    )
    assert result.returncode == 1  # pytest's "tests failed"
    assert result.returncode != UNTRUSTED_GREEN_RETURNCODE


def test_iteration_output_is_persisted(git_repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(sdk_spawners, "_repo_root", lambda: git_repo)
    monkeypatch.setenv("SPLOCK_TEST_PYTHON", sys.executable)
    plan_dir = git_repo / "plan"
    orch = _orchestrator(plan_dir, ["tests/test_green.py::test_ok"])

    run_verify_subprocess(
        slug="s", plan_dir=plan_dir, orchestrator_path=orch, iteration_n=3
    )
    assert (plan_dir / "_test_output_iter3.txt").is_file()
