"""T-D (SC-D #1) — venv-activation smoke test (renamed / absent venv).

SC-D #1 parameterizes the ~58 venv-activation sites (bin/ wrappers +
hooks/ scripts). The in-repo idiom is:

    VENV_PATH="${SPLOCK_VENV:-.venv}"
    if [ -f "$VENV_PATH/bin/activate" ] && [ -z "${VIRTUAL_ENV:-}" ]; then
        source "$VENV_PATH/bin/activate"
    fi

This test proves the parameterization is portable:

  1. Every bin/ wrapper + hooks/ script that activates a venv uses the
     ``${SPLOCK_VENV:-.venv}`` form (no hard-coded host venv path).
  2. With an ABSENT / RENAMED venv (``SPLOCK_VENV`` pointing at a
     non-existent dir) the wrapper still runs — it falls through to the
     ``python`` already on PATH rather than erroring on a missing
     ``activate`` script.
  3. With an active ``$VIRTUAL_ENV`` the activation block is skipped
     (idempotent — no double-source).

Run from the splock repo root with the project venv active.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Files that source a venv. The ~58 sites are ~40 extensionless bin/
# POSIX wrappers (bin/intent, bin/verify, ...) + ~18 hooks/ + bin/ .sh
# scripts. Collect both the .sh scripts AND the extensionless bin/ files.
def _venv_scripts() -> list[Path]:
    out: list[Path] = []
    out += list((REPO_ROOT / "hooks").glob("*.sh"))
    out += list((REPO_ROOT / "bin").glob("*.sh"))
    bin_dir = REPO_ROOT / "bin"
    for p in bin_dir.iterdir():
        if p.is_file() and p.suffix == "":  # extensionless wrapper
            out.append(p)
    return sorted(set(out))


VENV_SCRIPTS = _venv_scripts()

# The portable idiom: honor $SPLOCK_VENV, default to .venv. The activation
# must be gated on an INACTIVE $VIRTUAL_ENV.
_PARAM_RE = re.compile(r"\$\{SPLOCK_VENV:-\.venv\}")
_HARDCODED_HOST_VENV_RE = re.compile(r"/home/|/\.virtualenvs/|virtualenvs/")


def test_no_hardcoded_host_venv_path_in_any_script():
    """No script may hard-code a host venv path (e.g. ~/.virtualenvs/...)."""
    offenders: list[str] = []
    for s in VENV_SCRIPTS:
        text = s.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            if _HARDCODED_HOST_VENV_RE.search(line):
                offenders.append(f"{s.relative_to(REPO_ROOT)}:{i}: {line.strip()[:100]}")
    assert not offenders, "hard-coded host venv path found:\n" + "\n".join(offenders)


def test_venv_sourcing_scripts_use_parameterized_form():
    """Every script that references a venv activate path uses ${SPLOCK_VENV:-.venv}."""
    checked = 0
    for s in VENV_SCRIPTS:
        text = s.read_text(encoding="utf-8")
        if "bin/activate" not in text:
            continue
        checked += 1
        assert _PARAM_RE.search(text), (
            f"{s.relative_to(REPO_ROOT)} activates a venv but does not use the "
            "parameterized ${SPLOCK_VENV:-.venv} form"
        )
    # Sanity: we actually inspected the venv-activating scripts.
    assert checked >= 40, (
        f"expected >=40 venv-activating scripts, only inspected {checked} — "
        "the parameterization survey may be mis-scoped"
    )


def test_venv_activation_gated_on_inactive_virtual_env():
    """The activation guard must reference VIRTUAL_ENV so an active env is not re-sourced."""
    for s in VENV_SCRIPTS:
        text = s.read_text(encoding="utf-8")
        if "bin/activate" not in text:
            continue
        assert "VIRTUAL_ENV" in text, (
            f"{s.relative_to(REPO_ROOT)} sources a venv without guarding on "
            "VIRTUAL_ENV (non-idempotent activation)"
        )


def _run_wrapper(args, env):
    return subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_wrapper_runs_with_absent_renamed_venv():
    """With SPLOCK_VENV pointing at a non-existent dir, a wrapper still runs.

    Falls through to the python already on PATH (the test runner's venv)
    rather than failing on a missing activate script. We invoke a
    side-effect-free wrapper (``--help``).
    """
    env = dict(os.environ)
    env["SPLOCK_VENV"] = str(REPO_ROOT / "does_not_exist_venv_xyz")
    # Keep VIRTUAL_ENV so the wrapper uses the active interpreter on PATH.
    proc = _run_wrapper(["bin/orchestrator-next-ready", "--help"], env)
    assert proc.returncode == 0, (
        f"wrapper failed with absent SPLOCK_VENV: rc={proc.returncode} "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "usage:" in proc.stdout.lower()


def test_wrapper_runs_with_active_virtual_env_unset_splock_venv():
    """With SPLOCK_VENV unset (default .venv, which the repo has none of)
    the wrapper still runs via the active VIRTUAL_ENV on PATH."""
    env = dict(os.environ)
    env.pop("SPLOCK_VENV", None)
    # The repo ships no ./.venv; the active VIRTUAL_ENV interpreter is used.
    assert not (REPO_ROOT / ".venv").exists(), (
        "this test assumes the repo ships no committed ./.venv"
    )
    proc = _run_wrapper(["bin/orchestrator-next-ready", "--help"], env)
    assert proc.returncode == 0, (
        f"wrapper failed with default venv resolution: rc={proc.returncode} "
        f"stderr={proc.stderr!r}"
    )
