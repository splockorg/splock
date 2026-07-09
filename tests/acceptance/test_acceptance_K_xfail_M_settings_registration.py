"""K.4 (xfail) — §M F-01 settings.json registration for context-hygiene hooks.

Per inventory + §10 §7.4 (§M.impl.1 Status):
The `claude-md-discipline.sh` + `test-at-edit.sh` hooks need explicit
settings.json registration; Claude Code self-modification guard
blocked the agent write at code-phase.

When operator hand-applies the canonical §G.impl.13 block, this xpasses.
"""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


# Per §G.impl.13 spec: claude-md-discipline ships as a PreToolUse hook (not
# pre-commit only) so settings.json should have it registered.
EXPECTED_M_HOOKS_IN_SETTINGS = {
    "hooks/test-at-edit.sh",       # PostToolUse — already registered
    "hooks/claude-md-discipline.sh",  # pre-commit — operator-registered
}


@pytest.mark.xfail(
    reason="§M F-01 settings.json registration pending per §M.impl.1 Status",
    strict=False,
)
def test_settings_registers_all_M_hooks(repo_root):
    """K.4: settings.json registers both §M hook scripts."""
    settings = json.loads((repo_root / ".claude" / "settings.json").read_text(encoding="utf-8"))

    # Walk all hook command paths.
    all_commands: set[str] = set()
    for entries in settings.get("hooks", {}).values():
        for entry in entries:
            for h in entry.get("hooks", []):
                if h.get("command"):
                    all_commands.add(h["command"])

    missing = EXPECTED_M_HOOKS_IN_SETTINGS - all_commands
    assert not missing, (
        f"§M hooks not registered in settings.json: {sorted(missing)}\n"
        "Apply the canonical §G.impl.13 settings.json block per §M.impl Status."
    )
