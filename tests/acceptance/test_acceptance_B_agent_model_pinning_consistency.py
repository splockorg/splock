"""B.6 — Every `model:` frontmatter key in .claude/agents/*.md is a dated id.

Per inventory + quickstart §Subagent inventory cross-cutting rule #2:
when an agent .md frontmatter declares `model:`, the value MUST be a
dated identifier (e.g. `claude-haiku-4-5-20251001`), never a bare alias
(`sonnet`, `opus`, `haiku`). B.3 hardcodes verifier; this generalizes
across every agent that opts into a pin.

Agents that OMIT `model:` inherit from the parent (spec-correct per
quickstart §Subagent inventory — planner is intentionally omitted so
`OVERNIGHT_CHAIN_PLANNER_MODEL` env-var pin wins).
"""

from __future__ import annotations

import pytest
import re


pytestmark = pytest.mark.acceptance


FRONTMATTER_RE = re.compile(r"\A---\s*\n(.+?)\n---\s*\n", re.DOTALL)
# Matches e.g. "claude-haiku-4-5-20251001", "claude-opus-4-7-20260101".
DATED_MODEL_RE = re.compile(r"^claude-[a-z]+-\d+-\d+-\d{8}$")


def _parse_frontmatter(text: str) -> dict[str, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip()
    return out


def test_every_model_pinned_agent_uses_dated_identifier(repo_root):
    """B.6: any agent frontmatter `model:` value is a dated identifier."""
    agents_dir = repo_root / "agents"
    agent_files = sorted(p for p in agents_dir.glob("*.md") if p.name != "_roster.json")
    assert agent_files, "No .claude/agents/*.md files found — unexpected"

    bare_aliases: list[tuple[str, str]] = []
    for path in agent_files:
        fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
        model = fm.get("model")
        if not model:
            continue  # Inherits from parent — spec-correct, skip.
        if not DATED_MODEL_RE.match(model):
            bare_aliases.append((path.name, model))

    assert not bare_aliases, (
        "Agent frontmatter `model:` values that are not dated identifiers:\n"
        + "\n".join(f"  {name}: model={value!r}" for name, value in bare_aliases)
        + "\n\nBare aliases (`sonnet`, `opus`, `haiku`) drift silently per "
        "quickstart §Subagent inventory cross-cutting rule #2. Replace with "
        "the dated form (e.g. `claude-haiku-4-5-20251001`) or omit the key "
        "to inherit from the parent."
    )


def test_at_least_one_agent_is_model_pinned(repo_root):
    """B.6b: discipline check — at least one agent declares model: (avoids vacuously passing the rule)."""
    agents_dir = repo_root / "agents"
    pinned = []
    for path in agents_dir.glob("*.md"):
        fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
        if fm.get("model"):
            pinned.append(path.name)
    assert pinned, (
        "No agent has `model:` frontmatter pin — at least one (verifier per "
        "Risk 5) is required for the dated-identifier rule to mean anything."
    )
