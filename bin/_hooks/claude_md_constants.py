"""CLAUDE.md line-count thresholds (per implplan §M.impl.3).

Hand-authored repo discipline:
- Soft warning at 120 lines (operator-resolved target per plan §M.2).
- Hard ceiling at 200 lines (Anthropic-documented ceiling per plan §M.2).

Soft target applies to ROOT CLAUDE.md only. Nested CLAUDE.md files (per
§M.2a slim-down) enforce HARD_LINE_CEILING only — they naturally sit at
10–40 lines and the hook does not enforce a lower bound.
"""

from __future__ import annotations

SOFT_LINE_TARGET: int = 120
HARD_LINE_CEILING: int = 200

# `[force-claude-md]` token in commit message — operator override.
FORCE_TOKEN: str = "[force-claude-md]"


__all__ = ["SOFT_LINE_TARGET", "HARD_LINE_CEILING", "FORCE_TOKEN"]
