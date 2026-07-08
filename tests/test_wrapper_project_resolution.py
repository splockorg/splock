"""Wrapper-boundary regression: bin/* CLIs resolve the CALLER's project.

Every ``bin/*`` wrapper does ``cd "$REPO_ROOT"`` (the plugin/checkout root)
before ``exec python -m``, which would defeat ``project_root()``'s cwd
walk-up — and the plugin ships its own ``docs/plans/`` marker, so process
cwd would match the ephemeral cache itself. The wrappers therefore export
``SPLOCK_CALLER_PWD`` (their pre-``cd`` ``$PWD``) and the resolver starts
its walk there. These tests cross the actual wrapper boundary via
subprocess; the in-process tests in test_env_paths_project_root.py cannot
see this failure mode.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_WRAPPERS_WITH_CD = sorted(
    p
    for p in (REPO_ROOT / "bin").iterdir()
    if p.is_file() and 'cd "$REPO_ROOT"' in p.read_text(encoding="utf-8")
)


def _make_adopter(tmp_path: Path) -> Path:
    adopter = tmp_path / "adopter"
    plan_dir = adopter / "docs" / "plans" / "demo"
    plan_dir.mkdir(parents=True)
    (plan_dir / "demo_orchestrator.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {"id": "T1", "title": "first", "depends_on": []},
                    {"id": "T2", "title": "second", "depends_on": ["T1"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    (plan_dir / "_state.json").write_text(
        json.dumps({"tasks": {"T1": {"status": "done"}}}), encoding="utf-8"
    )
    return adopter


def _wrapper_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("SPLOCK_CALLER_PWD", None)
    # Skip the wrapper's venv activation and guarantee a `python` on PATH
    # (the test runner's interpreter), mirroring test_venv_activation_smoke.
    env["VIRTUAL_ENV"] = sys.prefix
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    return env


def test_every_cd_wrapper_exports_caller_pwd():
    missing = [
        p.name
        for p in _WRAPPERS_WITH_CD
        if 'export SPLOCK_CALLER_PWD="${SPLOCK_CALLER_PWD:-$PWD}"'
        not in p.read_text(encoding="utf-8")
    ]
    assert not missing, (
        f"wrappers cd to the plugin root without preserving the caller pwd: "
        f"{missing}"
    )


def test_picker_wrapper_resolves_the_callers_project(tmp_path):
    # The framework repo has no 'demo' slug, so a resolution bug surfaces
    # as exit 10 (slug not found under the plugin/checkout root) instead
    # of the correct exit 0 + next-ready id from the adopter's substrate.
    adopter = _make_adopter(tmp_path)
    proc = subprocess.run(
        [str(REPO_ROOT / "bin" / "orchestrator-next-ready"), "demo"],
        cwd=str(adopter),
        env=_wrapper_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert proc.stdout.strip() == "T2"


def test_existing_caller_pwd_is_not_clobbered(tmp_path):
    # Nested wrapper invocations (bin/verify -> bin/morning-review) must
    # keep the OUTERMOST caller's pwd: the export uses ${VAR:-$PWD}.
    adopter = _make_adopter(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = _wrapper_env()
    env["SPLOCK_CALLER_PWD"] = str(adopter)
    proc = subprocess.run(
        [str(REPO_ROOT / "bin" / "orchestrator-next-ready"), "demo"],
        cwd=str(elsewhere),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert proc.stdout.strip() == "T2"
