"""J.2 — `.claude/settings.json` hooks match canonical Anthropic schema.

Per inventory:
- Source: §10 §4.2 finding #6 (schema corrected from flat `{name, matcher, command}`
  to canonical `{matcher, hooks: [{type, command}]}`).
- Expected outcome: every hook event entry has `matcher` + `hooks` array;
  each `hooks` entry has `type: "command"` + `command: <path>`; no flat
  `{name, matcher, command}` survives.
"""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


CANONICAL_EVENT_KEYS = {"matcher", "hooks"}
CANONICAL_HOOK_KEYS = {"type", "command"}
LEGACY_FLAT_INDICATORS = {"name", "matcher", "command"}  # all 3 at top level = legacy


def test_settings_hooks_match_canonical_anthropic_shape(repo_root):
    """J.2: hooks blocks conform to {matcher, hooks: [{type, command}]} shape."""
    settings_path = repo_root / ".claude" / "settings.json"
    assert settings_path.exists(), f"Missing settings.json: {settings_path}"

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    hooks_block = settings.get("hooks", {})
    assert hooks_block, "settings.json has no 'hooks' top-level key"

    violations: list[str] = []
    for event, entries in hooks_block.items():
        if not isinstance(entries, list):
            violations.append(f"hooks.{event}: expected list, got {type(entries).__name__}")
            continue
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                violations.append(f"hooks.{event}[{i}]: expected dict, got {type(entry).__name__}")
                continue
            entry_keys = set(entry.keys())

            # Reject legacy flat shape: {name, matcher, command} at top level.
            if LEGACY_FLAT_INDICATORS.issubset(entry_keys) and "hooks" not in entry:
                violations.append(
                    f"hooks.{event}[{i}]: legacy flat shape "
                    f"{{name, matcher, command}} — must be canonical "
                    f"{{matcher, hooks: [{{type, command}}]}}"
                )
                continue

            missing = CANONICAL_EVENT_KEYS - entry_keys
            if missing:
                violations.append(
                    f"hooks.{event}[{i}]: missing canonical keys {sorted(missing)}"
                )
                continue

            inner = entry.get("hooks")
            if not isinstance(inner, list):
                violations.append(
                    f"hooks.{event}[{i}].hooks: expected list, got {type(inner).__name__}"
                )
                continue

            for j, h in enumerate(inner):
                if not isinstance(h, dict):
                    violations.append(f"hooks.{event}[{i}].hooks[{j}]: expected dict")
                    continue
                hmissing = CANONICAL_HOOK_KEYS - set(h.keys())
                if hmissing:
                    violations.append(
                        f"hooks.{event}[{i}].hooks[{j}]: missing keys {sorted(hmissing)}"
                    )
                if h.get("type") != "command":
                    violations.append(
                        f"hooks.{event}[{i}].hooks[{j}]: type must be 'command', "
                        f"got {h.get('type')!r}"
                    )

    assert not violations, "Settings.json hook shape violations:\n" + "\n".join(
        f"  - {v}" for v in violations
    )
