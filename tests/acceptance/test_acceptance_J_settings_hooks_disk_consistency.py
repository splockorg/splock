"""J.1 — Every `command` in `.claude/settings.json` hooks resolves to an existing file on disk.

Per inventory:
- Source: Sonnet B-1 + Opus B-4 + `verify_on_stop_gap.md` precedent.
- Predecessor: read-only inspection of `.claude/settings.json` + filesystem.
- Expected outcome: every hook script referenced in settings.json exists.

**This test is expected to fail today** until `hooks/verify-on-stop.sh`
ships per §10 §7.2. The xfail flips to xpass when operator authors the
script + commits.
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path


pytestmark = pytest.mark.acceptance


def _enumerate_hook_commands(settings: dict) -> list[tuple[str, str]]:
    """Walk `.claude/settings.json` and yield (event, command_path) tuples."""
    hooks = settings.get("hooks", {})
    out: list[tuple[str, str]] = []
    for event, entries in hooks.items():
        for entry in entries:
            for h in entry.get("hooks", []):
                cmd = h.get("command")
                if cmd:
                    out.append((event, cmd))
    return out


# 2026-05-22 (Pass 6): xfail removed — operator authored verify-on-stop.sh
# Option B stub per §10 §7.2; all hook commands now resolve on disk.
def test_every_hook_command_resolves_on_disk(repo_root):
    """J.1: every hook command in settings.json resolves to an existing file."""
    settings_path = repo_root / ".claude" / "settings.json"
    assert settings_path.exists(), f"Missing settings.json: {settings_path}"

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    entries = _enumerate_hook_commands(settings)
    assert entries, "settings.json has no hook command entries — unexpected"

    missing: list[tuple[str, str]] = []
    for event, cmd in entries:
        # Resolve relative paths against repo root.
        cmd_path = Path(cmd) if Path(cmd).is_absolute() else repo_root / cmd
        if not cmd_path.exists():
            missing.append((event, cmd))

    assert not missing, (
        "Hook commands in settings.json that don't exist on disk:\n"
        + "\n".join(f"  [{ev}] {c}" for ev, c in missing)
    )
