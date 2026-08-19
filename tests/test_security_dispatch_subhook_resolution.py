"""The umbrella dispatcher must resolve sub-hooks through `hooks_dir()`.

Regression fence for a silent fail-open. `security_dispatch.main()` hardcoded
`REPO_ROOT / ".claude" / "hooks"`, but this fork ships its hook scripts at the
top-level `hooks/` and an installed plugin has no `.claude/hooks/` at all.
`_run_subhook` returns `(0, "")` for a missing script — by design, so the
substrate can ship before `guardrail-spawn` lands — so every sealed-path,
package-safety and safe-ddl check silently ALLOWED. Nothing caught it: the
existing coverage (`tests/acceptance/test_acceptance_E_*`) drives each hook
script DIRECTLY, which is exactly the path that still worked.

These tests therefore drive `bin/security-dispatch.sh` — the entry point
`hooks/hooks.json` actually registers — and assert the deny reaches stdout.
Each fails without the fix. The benign control keeps a deny-everything
regression from reading as a pass, and the static fence keeps the hardcoded
layout from returning by a different route.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DISPATCHER = REPO_ROOT / "bin" / "security-dispatch.sh"


def _dispatch(event: dict, tmp_path: Path) -> subprocess.CompletedProcess:
    """Run the dispatcher as Claude Code does: event JSON on stdin.

    `cwd` is a scratch dir so nothing resolves against the caller's repo, and
    `CLAUDE_PLUGIN_ROOT` mirrors what Claude Code exports for a hook — the
    installed-plugin shape this defect was invisible in.

    The env INHERITS `os.environ` rather than being built from a literal: the
    sub-hooks emit through `bin/hook-log`, and conftest's session redirect of
    `HOOK_LOG_ROOT` / `CLI_LOG_ROOT` only reaches the child if it is carried.
    A literal here writes into the operator's real `~/.claude/logs`.
    """
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT)
    env["HOME"] = str(tmp_path)
    return subprocess.run(
        ["bash", str(DISPATCHER)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        env=env,
    )


def _denied(stdout: str) -> bool:
    if not stdout.strip():
        return False
    payload = json.loads(stdout)
    inner = payload.get("hookSpecificOutput", {})
    return inner.get("permissionDecision") == "deny"


@pytest.mark.parametrize(
    "label,event",
    [
        (
            "sealed-paths",
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "docs/plans/zz_dispatch_probe/_state.json",
                    "old_string": "a",
                    "new_string": "b",
                },
            },
        ),
        (
            "package-safety",
            {
                "tool_name": "Bash",
                # Assembled so the literal never appears in this file's source:
                # an adopter's own package-safety hook scans tool payloads, and
                # a repo running this suite under Claude Code would refuse the
                # pytest invocation itself.
                "tool_input": {"command": "p" + "ip " + "ins" + "tall requests"},
            },
        ),
        (
            "safe-ddl",
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "mysql -e '" + chr(68) + "ROP " + chr(84) + "ABLE zz'"
                },
            },
        ),
    ],
)
def test_dispatcher_routes_to_subhook(label, event, tmp_path):
    """Each guarded shape is refused THROUGH the dispatcher, not just directly."""
    result = _dispatch(event, tmp_path)
    assert result.returncode == 0, (
        f"dispatcher must exit 0 even on refusal (rc={result.returncode}):\n"
        f"{result.stderr}"
    )
    assert _denied(result.stdout), (
        f"{label} did not refuse through the dispatcher — sub-hook resolution "
        f"is failing open. stdout={result.stdout!r}"
    )


def test_dispatcher_allows_benign_edit(tmp_path):
    """Control: without this, a deny-everything dispatcher would pass above."""
    result = _dispatch(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "README.md",
                "old_string": "a",
                "new_string": "b",
            },
        },
        tmp_path,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "", (
        f"benign Edit was not silently allowed: {result.stdout!r}"
    )


def test_no_hardcoded_dotclaude_hooks_layout():
    """Static fence: the resolver is the only way to reach the scripts."""
    src = (REPO_ROOT / "bin" / "_hooks" / "security_dispatch.py").read_text()
    body = "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith("#")
    )
    _, _, after_docstring = body.partition('"""')
    _, _, code = after_docstring.partition('"""')
    assert '".claude"' not in code and ".claude/hooks" not in code, (
        "security_dispatch.py reintroduced a hardcoded hooks layout; resolve "
        "through bin._hooks.hooks_dir() instead"
    )
