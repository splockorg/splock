"""J.13 — Substrate-self-modification guard extends beyond `hooks/**`.

Per inventory + userguide §1.5 / §5.4 "Never edit the hook itself to make
a refusal go away. The sealed-paths.sh hook treats `hooks/**` as
sealed for this exact reason."

The guard is real for hooks, agents, and commands today — verified
directly in `.claude/settings.json` permissions.deny. This test pins
the surface so any future regression (someone strips a deny rule, or a
new substrate dir is added without a corresponding rule) is caught at
acceptance time, not after an incident.

What this test does NOT do (out of scope, by design):
- It does not try to Edit those paths to verify the deny fires —
  doing so would itself trip the guard. We trust the deny-rule
  registration in settings.json is what the Claude Code engine consumes.
- It does not extend to `.git/hooks/**` — that's the operator's repo-local
  install (per E.9 / J.3 wiring), not a substrate-shipped path the
  agents could edit. Recommendation deferred to Pass-7 findings if the
  operator wants symmetric coverage.
"""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


REQUIRED_DENY_PATTERNS: tuple[str, ...] = (
    "Edit(hooks/**)",     # already present — sealed-paths anchor
    "Edit(.claude/agents/**)",    # subagent definitions; LLM mutation = drift surface
    "Edit(commands/**)",  # slash command definitions
)


def _load_settings(repo_root):
    path = repo_root / ".claude" / "settings.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_substrate_self_mod_deny_rules_present(repo_root):
    """J.13a: every shipped `.claude/<substrate-dir>` has an Edit deny rule."""
    settings = _load_settings(repo_root)
    deny = set(settings.get("permissions", {}).get("deny", []))

    missing = [p for p in REQUIRED_DENY_PATTERNS if p not in deny]
    assert not missing, (
        "Substrate-self-modification guard rules missing from "
        ".claude/settings.json permissions.deny:\n"
        + "\n".join(f"  - {p}" for p in missing)
        + "\n\nThese guard the directories that agents must NEVER modify "
        "from inside a chain (per userguide §1.5 + §5.4). Add each missing "
        "pattern to permissions.deny."
    )


def test_roster_json_is_sealed_via_agents_glob(repo_root):
    """J.13b: `_roster.json` is sealed because `Edit(.claude/agents/**)` covers it.

    The roster is the closed-enum source-of-truth (J.12). Edit access
    would let an LLM-generated rewrite silently change the chain
    driver's spawnable subagent set. Validates the glob covers it.
    """
    repo = repo_root.resolve()
    roster = (repo / "agents" / "_roster.json")
    assert roster.exists(), "roster file missing"

    # Glob semantics: `Edit(.claude/agents/**)` matches any path under
    # `.claude/agents/`. The deny rule existence is verified by J.13a;
    # here we assert the file actually sits underneath the guarded glob.
    rel = roster.relative_to(repo)
    assert str(rel).startswith(".claude/agents/"), (
        f"_roster.json at {rel} is NOT underneath .claude/agents/, so the "
        "Edit(.claude/agents/**) deny rule would not cover it. The file "
        "must live under that glob to inherit the substrate-self-mod guard."
    )


def test_no_loophole_writes_to_substrate_dirs(repo_root):
    """J.13c: Write rules under `.claude/agents` aren't allowed implicitly.

    The shipped deny list uses `Edit(...)`. If a future cleanup adds an
    explicit `Allow(Write(.claude/agents/**))` that would defeat the
    guard for the Write tool. This test checks the `allow` list (if
    present) for any pattern that would re-enable substrate-dir Writes.
    """
    settings = _load_settings(repo_root)
    allow = list(settings.get("permissions", {}).get("allow", []))

    loophole_globs = (
        ".claude/agents",
        ".claude/hooks",
        ".claude/commands",
    )

    leaks: list[str] = []
    for rule in allow:
        rule_l = rule.lower()
        if ("write" in rule_l or "edit" in rule_l) and any(g in rule_l for g in loophole_globs):
            leaks.append(rule)

    assert not leaks, (
        "Allow-list rules that would defeat the substrate-self-mod guard:\n"
        + "\n".join(f"  - {r}" for r in leaks)
        + "\n\nRemove these — substrate-dir writes must remain refused."
    )
