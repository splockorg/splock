"""I.4 — `claude-md-discipline.sh` catches LLM-emission signature heuristics.

Per userguide §19 #3 + Risk 5 + plan §M.2: LLM-emitted persona/rule
files degrade task success by 20%+. The discipline hook must catch
the LLM-emission signature on commit.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def test_claude_md_discipline_module_has_llm_signature_check(repo_root):
    """I.4: bin/_hooks/claude_md_discipline.py references LLM-emission heuristics."""
    discipline_path = repo_root / "bin" / "_hooks" / "claude_md_discipline.py"
    if not discipline_path.exists():
        pytest.skip("bin/_hooks/claude_md_discipline.py not found")

    text = discipline_path.read_text(encoding="utf-8")
    # Look for heuristic-pattern indicators.
    indicators = ["llm", "emission", "regenerat", "heuristic", "auto-generated",
                  "ai-generated", "synthesized"]
    found = [s for s in indicators if s in text.lower()]
    assert found, (
        f"claude-md-discipline lacks any LLM-emission heuristic indicator; "
        f"expected at least one of: {indicators}\n"
        "Spec requires per userguide §19 #3 + Risk 5."
    )


def test_claude_md_discipline_module_imports_cleanly(repo_root):
    """I.4b: the discipline module imports without error (smoke check)."""
    try:
        from bin._hooks import claude_md_discipline
    except ImportError as exc:
        pytest.fail(f"claude_md_discipline module not importable: {exc}")
    # Module should expose some public API (functions or constants).
    public_names = [n for n in dir(claude_md_discipline) if not n.startswith("_")]
    assert public_names, "Module has no public API"
