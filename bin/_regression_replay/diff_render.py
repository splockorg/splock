"""Side-by-side diff renderer for operator review (§J.impl.6).

No auto-grader (per §J.impl.13 #1). Operator approves or rejects new
behavior visually.
"""

from __future__ import annotations

import json
from typing import Any


def render_side_by_side(
    *,
    case_id: str,
    expected_outcome: str,
    expected_outcome_details: dict[str, Any],
    materialized_files: list,
) -> str:
    lines: list[str] = []
    lines.append(f"# Regression replay: {case_id}")
    lines.append("")
    lines.append(f"## Expected outcome: `{expected_outcome}`")
    lines.append("")
    lines.append("### Expected outcome details")
    lines.append("```json")
    lines.append(
        json.dumps(expected_outcome_details, indent=2, sort_keys=True, ensure_ascii=False)
    )
    lines.append("```")
    lines.append("")
    lines.append("### Materialized inputs (tempdir)")
    for path in materialized_files:
        lines.append(f"- {path}")
    lines.append("")
    lines.append("### Operator decision")
    lines.append("Approve or reject; auto-grading deferred behind marker AGR.")
    return "\n".join(lines) + "\n"


__all__ = ["render_side_by_side"]
