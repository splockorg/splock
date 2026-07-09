"""B.2 — Each `.claude/agents/*.md` `description:` starts with its role name.

Per quickstart subagent cross-cutting rule #4 + plan §D.8: descriptions
follow the convention "recon for …", "plan for …" so invocation
surfaces match slash-command vocabulary.
"""

from __future__ import annotations

import pytest
import re


pytestmark = pytest.mark.acceptance


FRONTMATTER_RE = re.compile(r"\A---\s*\n(.+?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip()
    return fm


def test_every_agent_description_starts_with_role_name(repo_root):
    """B.2: each agent.md description begins with the role name."""
    agents_dir = repo_root / "agents"
    drift: list[tuple[str, str]] = []

    for md_path in sorted(agents_dir.glob("*.md")):
        fm = _parse_frontmatter(md_path.read_text(encoding="utf-8"))
        name = fm.get("name", md_path.stem)
        description = fm.get("description", "")
        # Description should start with `<name> for ...` or similar.
        if not description.startswith(name):
            drift.append((md_path.name, description[:80]))

    assert not drift, (
        "Agent descriptions don't start with the role name:\n"
        + "\n".join(f"  {fname}: {desc!r}" for fname, desc in drift)
    )
