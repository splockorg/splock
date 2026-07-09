"""B.3 — `verifier.md` requires a dated model pin in frontmatter.

Per quickstart subagent table + Risk 5: the verifier is Haiku-pinned
because spawns are high-frequency; the model identifier MUST be a
dated string (e.g. `claude-haiku-4-5-20251001`), never a bare alias.
"""

from __future__ import annotations

import pytest
import re


pytestmark = pytest.mark.acceptance


FRONTMATTER_RE = re.compile(r"\A---\s*\n(.+?)\n---\s*\n", re.DOTALL)
DATED_MODEL_RE = re.compile(r"^claude-[a-z]+-\d+-\d+-\d{8}$")


def test_verifier_md_has_dated_model_pin(repo_root):
    """B.3: verifier.md frontmatter has model: <dated-anthropic-id>."""
    verifier_path = repo_root / "agents" / "verifier.md"
    assert verifier_path.exists(), "verifier.md missing"

    text = verifier_path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    assert m, "verifier.md missing frontmatter delimiters"

    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip()

    model = fm.get("model")
    assert model, "verifier.md has no `model:` frontmatter key"
    assert DATED_MODEL_RE.match(model), (
        f"verifier model pin must be dated identifier (e.g. claude-haiku-4-5-20251001); "
        f"got {model!r} — bare aliases drift silently per quickstart rule #2"
    )
