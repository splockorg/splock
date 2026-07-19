"""B.1 — All seven `.claude/agents/*.md` files parse as valid frontmatter + body.

Per inventory:
- Source: userguide §3 + Opus M-substrate fixture-realism.
- Predecessor: read-only file inspection; no fixtures.
- Expected outcome: each of 7 agent .md files parses; YAML frontmatter
  has required keys; body is non-empty.
"""

from __future__ import annotations

import json
import pytest
import re


pytestmark = pytest.mark.acceptance


# v2 (2026-05-24): added `qna` (question-and-answer subagent) per the
# qa-vs-qna terminology note and _roster.json schema bump to v2.
# v3 (2026-07-18): added `eli5` (plainspeak-translation lens) per
# docs/feedback_eli5_terminology.md and _roster.json schema bump to v3.
EXPECTED_AGENTS = {"planner", "recon", "qa", "qna", "research", "coder", "reviewer", "verifier", "eli5"}
REQUIRED_FRONTMATTER_KEYS = {"name", "description", "tools"}
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.+?)\n---\s*\n(.*)\Z", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Tiny YAML frontmatter parser — only handles `key: value` lines (no nesting).

    Sufficient for agent definition files which use flat frontmatter.
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("No frontmatter delimiters (---...---) found")
    fm_block, body = m.group(1), m.group(2)
    fm = {}
    for line in fm_block.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm, body


def test_all_seven_agents_present_and_parse(repo_root):
    """B.1: every agent .md file parses + has required frontmatter keys."""
    agents_dir = repo_root / "agents"
    assert agents_dir.is_dir(), f"Missing agents dir: {agents_dir}"

    md_files = sorted(agents_dir.glob("*.md"))
    found_names = set()
    failures = []

    for md_path in md_files:
        text = md_path.read_text(encoding="utf-8")
        try:
            fm, body = _parse_frontmatter(text)
        except ValueError as exc:
            failures.append(f"{md_path.name}: frontmatter parse failed — {exc}")
            continue

        missing = REQUIRED_FRONTMATTER_KEYS - set(fm.keys())
        if missing:
            failures.append(f"{md_path.name}: missing frontmatter keys {sorted(missing)}")
            continue

        if not body.strip():
            failures.append(f"{md_path.name}: empty body")
            continue

        found_names.add(fm.get("name", md_path.stem))

    assert not failures, "Agent definition parse failures:\n" + "\n".join(failures)
    assert found_names == EXPECTED_AGENTS, (
        f"Agent set mismatch: expected {sorted(EXPECTED_AGENTS)}; "
        f"found {sorted(found_names)}"
    )

    # Roster JSON enum must match the file inventory (anticipates B.4).
    roster_path = agents_dir / "_roster.json"
    assert roster_path.exists(), "Missing _roster.json source-of-truth"
    roster = json.loads(roster_path.read_text(encoding="utf-8"))
    roster_set = set(roster.get("subagents", []))
    assert roster_set == EXPECTED_AGENTS, (
        f"_roster.json subagents drift: expected {sorted(EXPECTED_AGENTS)}; "
        f"got {sorted(roster_set)}"
    )
