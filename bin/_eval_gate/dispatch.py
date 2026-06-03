"""Dispatch matrix for `bin/eval-gate` (§J.impl.9 lines 6976-6995).

| Condition | Branch | Outcome |
|---|---|---|
| SPLOCK_CHAIN_ID unset AND touch-path match | Interactive operator | strict mode |
| SPLOCK_CHAIN_ID set AND touch-path match | Chain-driver | report-only |
| No touch-path | n/a | exit 0 silently |
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Dispatch:
    branch: str  # "interactive_strict" | "chain_report_only" | "no_touch_path"
    chain_id: Optional[str]
    plan_slug: Optional[str]


def resolve(touch_path_matched: bool) -> Dispatch:
    chain_id = os.environ.get("SPLOCK_CHAIN_ID") or None
    plan_slug = os.environ.get("SPLOCK_PLAN_SLUG") or None
    if not touch_path_matched:
        return Dispatch(branch="no_touch_path", chain_id=chain_id, plan_slug=plan_slug)
    if chain_id:
        return Dispatch(
            branch="chain_report_only", chain_id=chain_id, plan_slug=plan_slug
        )
    return Dispatch(branch="interactive_strict", chain_id=chain_id, plan_slug=plan_slug)


__all__ = ["Dispatch", "resolve"]
