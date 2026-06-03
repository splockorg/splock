"""System touch-path closed-set + matcher (§J.impl.9).

Product-code commits (`extraction/`, `crawler/`, `console/`) cannot
regress the splock system's failure-detection capabilities;
the gate no-ops. System-internal commits CAN regress those
capabilities; the gate exists for those.
"""

from __future__ import annotations

import pathlib
from typing import Iterable


# Closed enum (per §J.impl.9). One glob pattern per entry.
SYSTEM_TOUCH_PATHS: tuple[str, ...] = (
    # Category (1) — .claude/ substrate
    ".claude/commands/**",
    ".claude/hooks/**",
    ".claude/agents/**",
    # Category (2) — top-level system CLIs
    "bin/chain-overnight",
    "bin/update_orchestrator",
    "bin/ralph-check",
    "bin/marker",
    "bin/route_issue",
    "bin/morning-review",
    "bin/render_plan",
    "bin/verify_plan",
    "bin/verify",
    "bin/eval-gate",
    "bin/eval-trend",
    "bin/eval-baseline",
    "bin/render_spans",
    "bin/regression-replay",
    # Category (3) — implementation packages
    "bin/_chain_overnight/**",
    "bin/_update_orchestrator/**",
    "bin/_marker/**",
    "bin/_route_issue/**",
    "bin/_morning_review/**",
    "bin/_planner/**",
    "bin/_retry_loop/**",
    "bin/_jsonl_log/**",
    "bin/_env_inventory/**",
    "bin/_eval_common/**",
    "bin/_eval_gate/**",
    "bin/_eval_trend/**",
    "bin/_eval_baseline/**",
    "bin/_render_spans/**",
    "bin/_regression_replay/**",
    # Category (4) — schemas + rubric configs
    "schemas/**",
)


def is_system_touch(staged_files: Iterable[str]) -> bool:
    """Return True if any staged file matches the closed-set."""
    for raw in staged_files:
        s = raw.strip()
        if not s:
            continue
        path = pathlib.PurePath(s)
        for pattern in SYSTEM_TOUCH_PATHS:
            # Prefer full-match against the path; fall back to suffix-match
            # for the simple file entries.
            if path.match(pattern):
                return True
            # `match` is right-anchored — covers `foo/bar/**` correctly when
            # called with the full path; for bare-name patterns like
            # "bin/marker" we also accept exact equality.
            if str(path) == pattern:
                return True
    return False


__all__ = ["SYSTEM_TOUCH_PATHS", "is_system_touch"]
