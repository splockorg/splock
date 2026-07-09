"""J.12 — `_roster.json` subagents ↔ orchestrator_v1 `subagent_type` enum identity.

Per inventory:
- Source: userguide §3 "Hand-authored alongside the seven .claude/agents/*.md
  definitions per plan §D.8 cross-cutting rule #4 (no LLM emission)";
  `_roster.json::_comment`: "Source-of-truth enum for
  orchestrator_v1.schema.json agent_assignment.subagent."
- Expected outcome: the closed-enum surface for `subagent` in
  orchestrator_v1 is the roster file (the schema description points at
  the roster file; the schema doesn't duplicate the enum). The schema's
  `subagent` field has `minLength: 1` and a description naming the
  roster as the closed-enum source. This test verifies that the roster
  IS the source of truth AND that every roster member is reachable as
  an agent definition file, so the chain driver never spawns a
  subagent_type that has no roster row.

Drift in either direction is a load-bearing bug:
- Roster has an entry without a `.claude/agents/<name>.md` → chain
  spawns a missing agent (silent failure).
- An agent .md exists not in roster → schema rejects valid task assignments.
"""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


def _load_roster(repo_root):
    path = repo_root / "agents" / "_roster.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_orchestrator_schema(repo_root):
    path = repo_root / "schemas" / "orchestrator_v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _agent_md_names(repo_root) -> set[str]:
    agents_dir = repo_root / "agents"
    return {p.stem for p in agents_dir.glob("*.md")}


def test_roster_subagents_equals_agent_md_file_set(repo_root):
    """J.12a: `_roster.json::subagents` ≡ set of `.claude/agents/*.md` stems."""
    roster = _load_roster(repo_root)
    roster_set = set(roster.get("subagents", []))
    md_set = _agent_md_names(repo_root)

    roster_only = roster_set - md_set
    md_only = md_set - roster_set

    assert not roster_only and not md_only, (
        "Drift between `_roster.json::subagents` and `.claude/agents/*.md` stems:\n"
        f"  in roster but no .md file: {sorted(roster_only)}\n"
        f"  in .md files but no roster entry: {sorted(md_only)}\n"
        "Chain driver would either spawn a missing agent (roster-only) or refuse "
        "valid plan assignments (md-only). Hand-edit both sides per §D.8 rule #4 "
        "(no LLM emission)."
    )


def test_orchestrator_schema_points_at_roster_as_enum_source(repo_root):
    """J.12b: schema's `subagent` field description names `_roster.json` as enum source.

    Per the schema's design choice (line: `"description": "Closed enum source
    is .claude/agents/_roster.json per §D.impl; schema does not duplicate
    the enum to avoid drift."`), the test verifies the schema still defers
    to the roster rather than duplicating the enum.
    """
    schema = _load_orchestrator_schema(repo_root)
    task_props = (
        schema.get("properties", {}).get("tasks", {})
              .get("items", {}).get("properties", {})
    )
    agent_assignment = task_props.get("agent_assignment", {})
    subagent = agent_assignment.get("properties", {}).get("subagent", {})

    assert subagent, (
        "orchestrator_v1.schema.json missing tasks.items.agent_assignment.subagent — "
        "schema shape changed; roster ↔ schema binding may have moved."
    )

    # The schema must NOT duplicate the enum (otherwise drift is inevitable).
    assert "enum" not in subagent, (
        "schema `subagent` property defines its own `enum` — duplicates the roster "
        "and will drift. Per §D.impl design: schema description names the roster "
        "as source-of-truth; the schema itself uses minLength: 1 + free string."
    )

    desc = subagent.get("description", "").lower()
    assert "_roster.json" in desc or "roster" in desc, (
        "schema `subagent` description must name `.claude/agents/_roster.json` as the "
        "closed-enum source per §D.impl. Found description: "
        f"{subagent.get('description')!r}"
    )


def test_no_extra_keys_in_roster_alongside_subagents(repo_root):
    """J.12c: roster file shape — schema_version + subagents + _comment only.

    Guards against accidental key additions that downstream consumers
    might silently ignore (or worse, treat as a second source of truth).
    """
    roster = _load_roster(repo_root)
    expected = {"schema_version", "subagents", "_comment"}
    extras = set(roster.keys()) - expected
    assert not extras, (
        f"_roster.json has unexpected top-level keys: {sorted(extras)} "
        f"(expected exactly {sorted(expected)})"
    )
