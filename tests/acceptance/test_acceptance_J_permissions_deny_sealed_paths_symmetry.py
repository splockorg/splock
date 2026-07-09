"""J.4 — `permissions.deny` ↔ `sealed_paths.txt` symmetry audit.

Per inventory:
- Source: Opus M-6 — every path under `sealed_paths.txt` should have a
  matching deny rule in `.claude/settings.json` (and vice versa, modulo
  intentional differences).
- Expected outcome: symmetric coverage for sealed-state paths; differences
  documented in an allowlist.
"""

from __future__ import annotations

import json
import pytest
import re
from pathlib import Path


pytestmark = pytest.mark.acceptance


# Intentional asymmetries — paths that ONLY appear in one surface for design reasons.
# Document with rationale; future paths added to either side should be reviewed
# against this allowlist.
INTENTIONAL_ASYMMETRIES = {
    # settings.deny additions not in sealed_paths.txt: argv-driven controls
    "deny_only": {
        "Edit(.env*)",
        "Edit(.git/**)",
        "Edit(.claude/agents/**)",
        "Edit(hooks/**)",
        "Edit(commands/**)",
        # Settings-deny uses glob form per file; sealed_paths.txt uses path globs
        # — comparison is below the file-pattern layer.
    },
    # sealed_paths additions not in settings.deny: user-home + plan-dir patterns
    # handled by the hook script's content check (not by settings-deny).
    "sealed_only": set(),  # populated below from sealed_paths.txt patterns
}


def _read_sealed_paths(repo_root: Path) -> set[str]:
    text = (repo_root / "hooks" / "sealed_paths.txt").read_text(encoding="utf-8")
    out = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.add(line)
    return out


def _read_settings_deny(repo_root: Path) -> set[str]:
    settings = json.loads((repo_root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    return set(settings.get("permissions", {}).get("deny", []))


def _path_from_deny_rule(rule: str) -> str | None:
    """`Edit(docs/plans/*/_state.json)` → `docs/plans/*/_state.json`."""
    m = re.match(r"^(Edit|Write|Read)\((.+)\)$", rule)
    return m.group(2) if m else None


def test_critical_sealed_state_paths_have_settings_deny_coverage(repo_root):
    """J.4: the critical sealed-state path patterns also appear in settings.deny.

    Not a 1:1 symmetry — the two surfaces have different responsibilities.
    sealed_paths.txt is consumed by the hook scripts for runtime refusal;
    settings.deny is consumed by the Claude Code permissions engine. The
    overlap should cover the high-blast-radius sealed-state inventory.
    """
    sealed = _read_sealed_paths(repo_root)
    deny_rules = _read_settings_deny(repo_root)
    deny_paths = {_path_from_deny_rule(r) for r in deny_rules}
    deny_paths.discard(None)

    # Critical paths that MUST be in both surfaces.
    # Each sealed_paths.txt pattern that begins with `docs/plans/*/_` is
    # high-blast-radius and should also have a settings.deny rule.
    critical_sealed = {
        s for s in sealed
        if s.startswith("docs/plans/*/_") or s.startswith("docs/intent/")
    }

    missing_in_deny: list[str] = []
    for cs in critical_sealed:
        # Check if any deny rule's path matches this sealed pattern.
        if not any(cs == dp or cs in dp or dp in cs for dp in deny_paths):
            missing_in_deny.append(cs)

    assert not missing_in_deny, (
        "Critical sealed_paths.txt patterns missing from settings.deny:\n"
        + "\n".join(f"  - {p}" for p in missing_in_deny)
    )


def test_settings_deny_intentional_asymmetries_documented(repo_root):
    """J.4b: deny-only rules match the documented intentional-asymmetry allowlist."""
    deny_rules = _read_settings_deny(repo_root)
    sealed = _read_sealed_paths(repo_root)
    sealed_paths = {p for p in sealed}

    # Compute deny rules whose path doesn't appear in sealed_paths.txt at all.
    deny_only_actual = set()
    for rule in deny_rules:
        path = _path_from_deny_rule(rule)
        if path and not any(p == path or p in path or path in p for p in sealed_paths):
            deny_only_actual.add(rule)

    undocumented = deny_only_actual - INTENTIONAL_ASYMMETRIES["deny_only"]
    assert not undocumented, (
        "Undocumented deny-only rules (not in INTENTIONAL_ASYMMETRIES allowlist):\n"
        + "\n".join(f"  - {r}" for r in undocumented)
        + "\n\nIf intentional, add to INTENTIONAL_ASYMMETRIES['deny_only']."
    )
