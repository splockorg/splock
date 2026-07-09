"""B.5 — No agent definition exposes tools that would let it spawn subagents.

Per userguide §3 cross-cutting rule (subagents-cannot-spawn-subagents):
the Agent / Task tools must NOT appear in any agent's `tools:` frontmatter
list. Platform constraint per plan §D.8 + Risk 5.
"""

from __future__ import annotations

import pytest
import re


pytestmark = pytest.mark.acceptance


FRONTMATTER_RE = re.compile(r"\A---\s*\n(.+?)\n---\s*\n", re.DOTALL)
SPAWN_TOOLS = {"Agent", "Task", "TaskCreate", "Subagent"}


def test_no_agent_has_subagent_spawning_tool(repo_root):
    """B.5: no .claude/agents/*.md tools frontmatter includes Agent/Task."""
    agents_dir = repo_root / "agents"
    violations: list[tuple[str, str]] = []

    for md_path in sorted(agents_dir.glob("*.md")):
        text = md_path.read_text(encoding="utf-8")
        m = FRONTMATTER_RE.match(text)
        if not m:
            continue
        fm_block = m.group(1)
        # Find the tools: line.
        for line in fm_block.splitlines():
            if line.lstrip().startswith("tools:"):
                tools_value = line.partition(":")[2].strip()
                # Comma-separated list.
                tools = {t.strip() for t in tools_value.split(",") if t.strip()}
                violating = SPAWN_TOOLS & tools
                if violating:
                    violations.append((md_path.name, ",".join(sorted(violating))))

    assert not violations, (
        "Agent definitions include subagent-spawning tools (violates plan §D.8 + Risk 5):\n"
        + "\n".join(f"  {fname}: {tools}" for fname, tools in violations)
    )
