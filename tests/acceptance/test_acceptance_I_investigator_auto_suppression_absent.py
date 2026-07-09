"""I.5 — `extraction/investigator/intake.py::_resolve_context` does not auto-suppress agent runs.

Per userguide §19 #6 + extraction/CLAUDE.md: agent-driven investigator
runs are no longer auto-suppressed (production-default since 2026-05-07).
The substrate trusts the agent's intake decision; hooks catch downstream
violations.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def test_investigator_resolve_context_does_not_auto_suppress(repo_root):
    """I.5: _resolve_context doesn't have an auto-suppress branch for agent runs."""
    intake_path = repo_root / "extraction" / "investigator" / "intake.py"
    if not intake_path.exists():
        pytest.skip("investigator/intake.py not present (different repo layout?)")

    text = intake_path.read_text(encoding="utf-8")

    # Look for suppression-shaped patterns that would auto-suppress agent runs.
    suppression_indicators = [
        "auto_suppress",
        "suppress_agent",
        "agent_suppressed",
        "if.*agent.*:.*return.*None",  # crude conditional-return pattern
    ]
    import re
    found_real = []
    for pat in suppression_indicators:
        matches = re.findall(pat, text)
        if matches:
            # Filter out obvious negation/comment context.
            for m in matches:
                # Quick heuristic: check the line — is it a comment?
                for line in text.splitlines():
                    if m in line and not line.strip().startswith("#"):
                        found_real.append((pat, line.strip()[:100]))
                        break

    assert not found_real, (
        f"investigator/intake.py has suppression-shaped code that may auto-suppress "
        f"agent runs (userguide §19 #6 says these were removed):\n"
        + "\n".join(f"  {p}: {l!r}" for p, l in found_real)
    )
