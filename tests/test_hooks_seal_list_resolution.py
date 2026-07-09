"""The seal inventory must be found, or the guards fail open.

`sealed_paths_hook` and `sealed_delete_hook` both end their inventory load with:

    except FileNotFoundError:
        _hook_log("error", "sealed_paths.txt missing; allowing")
        return 0

That is a deliberate, documented fail-open: no inventory means no defense. It is
only safe if the inventory is *always found*. Both modules hardcoded upstream's
`.claude/hooks/sealed_paths.txt`; this fork ships `hooks/sealed_paths.txt`. So
they never found it, always took that branch, and silently permitted every write
and delete they exist to refuse.

`hook_lint` had the same layout assumption from the other side: it scanned
`.claude/hooks/`, found nothing, and reported `0 hooks PASS` — a green light from
a linter that examined zero files, which is exactly how the two guards stayed
broken without anyone noticing.

Resolution now lives in `bin._hooks.sealed_paths_file` / `bin._hooks.hooks_dir`,
once, so the mistake cannot be repeated per-module.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bin._env_paths import plugin_root
from bin._hooks import hooks_dir, sealed_paths_file

_SEALED_WRITE = {
    "tool_name": "Write",
    "tool_input": {"file_path": "docs/plans/demo/_state.json", "content": "{}"},
}
_PLAIN_WRITE = {
    "tool_name": "Write",
    "tool_input": {"file_path": "src/module.py", "content": "x = 1"},
}
_SEALED_DELETE = {
    "tool_name": "Bash",
    "tool_input": {"command": "rm docs/plans/demo/_state.json"},
}


def _run_hook(module: str, event: dict, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", module],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        cwd=str(cwd or plugin_root()),
    )


def _is_deny(stdout: str) -> bool:
    if not stdout.strip():
        return False
    payload = json.loads(stdout)
    return payload["hookSpecificOutput"]["permissionDecision"] == "deny"


# --------------------------------------------------------------------------- #
# resolution                                                                    #
# --------------------------------------------------------------------------- #


def test_the_default_seal_inventory_actually_exists() -> None:
    """The load-bearing assertion. A missing inventory disables both guards."""
    assert sealed_paths_file().exists()


def test_hooks_dir_is_top_level_not_dot_claude() -> None:
    assert hooks_dir() == plugin_root() / "hooks"
    assert list(hooks_dir().glob("*.sh")), "no hook scripts found"
    assert not (plugin_root() / ".claude" / "hooks").exists()


def test_the_env_override_wins(tmp_path, monkeypatch) -> None:
    custom = tmp_path / "custom_seals.txt"
    custom.write_text("docs/plans/*/_state.json\n", encoding="utf-8")
    monkeypatch.setenv("SPLOCK_SEALED_PATHS_FILE", str(custom))
    assert sealed_paths_file() == custom


def test_an_adopter_supplied_list_is_preferred_over_the_shipped_one(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("SPLOCK_SEALED_PATHS_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "sealed_paths.txt").write_text("x\n", encoding="utf-8")
    assert sealed_paths_file() == Path("hooks/sealed_paths.txt")


# --------------------------------------------------------------------------- #
# the guards actually guard                                                     #
# --------------------------------------------------------------------------- #


def test_sealed_paths_hook_denies_a_write_to_sealed_state() -> None:
    result = _run_hook("bin._hooks.sealed_paths_hook", _SEALED_WRITE)
    assert result.returncode == 0, result.stderr
    assert _is_deny(result.stdout), f"failed open: stdout={result.stdout!r}"


def test_sealed_paths_hook_still_allows_an_ordinary_write() -> None:
    """The guard must not become a blanket refusal."""
    result = _run_hook("bin._hooks.sealed_paths_hook", _PLAIN_WRITE)
    assert result.returncode == 0
    assert not result.stdout.strip()


def test_sealed_delete_hook_denies_rm_of_sealed_state() -> None:
    result = _run_hook("bin._hooks.sealed_delete_hook", _SEALED_DELETE)
    assert result.returncode == 0, result.stderr
    assert _is_deny(result.stdout), f"failed open: stdout={result.stdout!r}"


@pytest.mark.parametrize(
    "module", ["bin._hooks.sealed_paths_hook", "bin._hooks.sealed_delete_hook"]
)
def test_a_guard_invoked_from_a_foreign_cwd_still_finds_its_inventory(
    module: str, tmp_path
) -> None:
    """Hooks run with the AGENT's cwd, which is rarely the plugin root.

    The old cwd-relative candidate was the only thing that ever matched upstream;
    losing it must not lose the guard.
    """
    event = _SEALED_WRITE if module.endswith("paths_hook") else _SEALED_DELETE
    result = subprocess.run(
        [sys.executable, "-m", module],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env={
            "PYTHONPATH": str(plugin_root()),
            "PATH": "/usr/bin:/bin",
            # A minimal env drops the session-wide log-root redirect, and the
            # hook's forensic `_hook_log` call would then append test rows to the
            # operator's real ~/.claude/logs/. Carry it through explicitly.
            **{
                k: os.environ[k]
                for k in ("HOOK_LOG_ROOT", "CLI_LOG_ROOT")
                if k in os.environ
            },
        },
    )
    assert _is_deny(result.stdout), f"failed open from {tmp_path}: {result.stdout!r}"


# --------------------------------------------------------------------------- #
# the linter that should have caught this                                       #
# --------------------------------------------------------------------------- #


def test_hook_lint_lints_a_nonzero_number_of_hooks() -> None:
    """`0 hooks PASS` is not a pass. It is a linter that found nothing to lint."""
    result = subprocess.run(
        [sys.executable, "-m", "bin._hooks.hook_lint", "--check"],
        capture_output=True,
        text=True,
        cwd=str(plugin_root()),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    count = int(result.stdout.split("hook-lint:")[1].split()[0])
    assert count >= len(list(hooks_dir().glob("*.sh"))) > 0
