"""T-D (SC-D #2) — hooks/hooks.json validation + post-install hook-fire.

SC-D #2: the host ``settings.json`` 7-event hooks block is re-expressed
in ``hooks/hooks.json`` with EVERY script path rewritten to
``${CLAUDE_PLUGIN_ROOT}/...`` (double-quoted shell-form), and the
provisional ``std-*`` hook scripts renamed to ``splock-*`` in lockstep.

This test covers two halves:

  A. *Validate* — structural validation of ``hooks/hooks.json`` (the
     portable equivalent of ``claude plugin validate``): valid JSON,
     every command path is ``${CLAUDE_PLUGIN_ROOT}``-relative, and every
     referenced script file exists on disk and is executable. It also
     asserts NO ``std-*`` script reference survives (rename lockstep).

  B. *Post-install hook-fire* — actually invoke each renamed lifecycle
     hook script with a synthetic Claude Code event envelope on stdin
     and assert it exits 0 (the fail-open lifecycle contract) without
     a traceback on stderr. This proves the renamed scripts are wired +
     runnable, i.e. an adopter installing the plugin gets working hooks.

Run from the splock repo root with the project venv active.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS_JSON = REPO_ROOT / "hooks" / "hooks.json"

PLUGIN_ROOT_TOKEN = "${CLAUDE_PLUGIN_ROOT}/"


def _all_command_strings(hooks_block: dict) -> list[str]:
    cmds: list[str] = []
    for _event, groups in hooks_block.get("hooks", {}).items():
        for group in groups:
            for hook in group.get("hooks", []):
                if hook.get("type") == "command":
                    cmds.append(hook["command"])
    return cmds


# ---------------------------------------------------------------------------
# A. Validate
# ---------------------------------------------------------------------------


def test_hooks_json_is_valid_json():
    json.loads(HOOKS_JSON.read_text(encoding="utf-8"))


def test_every_command_is_plugin_root_relative():
    """Every hook command path is ``${CLAUDE_PLUGIN_ROOT}``-relative."""
    block = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    cmds = _all_command_strings(block)
    assert cmds, "hooks.json declares no command hooks"
    bad = [c for c in cmds if not c.startswith(PLUGIN_ROOT_TOKEN)]
    assert not bad, (
        "hook command(s) not ${CLAUDE_PLUGIN_ROOT}-relative:\n" + "\n".join(bad)
    )


def test_no_std_prefixed_script_reference_survives():
    """The rename lockstep: no ``std-*`` script path may remain in hooks.json."""
    text = HOOKS_JSON.read_text(encoding="utf-8")
    assert "std-" not in text, (
        "hooks.json still references a provisional std-* script — rename not in lockstep"
    )
    # And the canonical lifecycle scripts are the renamed splock-* forms.
    for expected in (
        "splock-session-start.sh",
        "splock-stop.sh",
        "splock-user-prompt-submit.sh",
        "splock-subagent-stop.sh",
        "splock-session-end.sh",
    ):
        assert expected in text, f"hooks.json missing expected script {expected}"


def test_every_referenced_script_exists_and_is_executable():
    """Each ${CLAUDE_PLUGIN_ROOT}-relative script resolves on disk + is +x."""
    block = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    missing: list[str] = []
    not_exec: list[str] = []
    for cmd in _all_command_strings(block):
        rel = cmd[len(PLUGIN_ROOT_TOKEN):]
        target = REPO_ROOT / rel
        if not target.exists():
            missing.append(rel)
        elif not os.access(target, os.X_OK):
            not_exec.append(rel)
    assert not missing, "hooks.json references missing scripts:\n" + "\n".join(missing)
    assert not not_exec, "hook scripts not executable (+x):\n" + "\n".join(not_exec)


# ---------------------------------------------------------------------------
# B. Post-install hook-fire
# ---------------------------------------------------------------------------

# Renamed lifecycle hooks + a representative synthetic envelope each.
_SESSION_ENVELOPE = json.dumps({"session_id": "sess_2026-01-01T00:00:00Z_abcd"})
_PROMPT_ENVELOPE = json.dumps(
    {"session_id": "sess_2026-01-01T00:00:00Z_abcd", "prompt": "hello"}
)

FIRE_CASES = [
    ("hooks/splock-session-start.sh", _SESSION_ENVELOPE),
    ("hooks/splock-session-end.sh", _SESSION_ENVELOPE),
    ("hooks/splock-stop.sh", _SESSION_ENVELOPE),
    ("hooks/splock-subagent-stop.sh", _SESSION_ENVELOPE),
    ("hooks/splock-user-prompt-submit.sh", _PROMPT_ENVELOPE),
]


@pytest.mark.parametrize("script_rel,envelope", FIRE_CASES)
def test_renamed_lifecycle_hook_fires_exit_zero(script_rel, envelope, tmp_path):
    """Each renamed lifecycle hook fires fail-open (exit 0) on a synthetic
    envelope, with no Python traceback leaking to stderr."""
    script = REPO_ROOT / script_rel
    assert script.exists(), f"renamed hook missing: {script_rel}"
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_DATA"] = str(tmp_path)
    proc = subprocess.run(
        ["bash", str(script)],
        cwd=str(REPO_ROOT),
        input=envelope,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"{script_rel} did not exit 0 (lifecycle hooks are fail-open): "
        f"rc={proc.returncode} stderr={proc.stderr[:300]!r}"
    )
    assert "Traceback (most recent call last)" not in proc.stderr, (
        f"{script_rel} leaked a Python traceback:\n{proc.stderr[:500]}"
    )
