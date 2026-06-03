"""Four-way rubric — deterministic refusal codes (implplan §L.impl.5).

Codifies plan §L.4 lines 3896–3922 as a function chain with closed-enum
return.

Decision tree:

  1. Match category against closed enum {fix-now, outstanding, marker, tier-promote}.
  2. If match → dispatch to handler module.
  3. If no match AND no trigger fired → exit 28
     (`rubric_refuse_no_category_fits`).

Critical ordering: triggers fire FIRST per `main.py` dispatch; rubric is
structurally unreachable on forced-trigger paths. `escalate` is NOT one of
the four rubric categories — it is the FORCED outcome when a trigger
fires (or the operator-direct shape via `--type escalate`, which bypasses
the rubric).
"""

from __future__ import annotations

import dataclasses
from typing import Literal


RubricCategory = Literal["fix-now", "outstanding", "marker", "tier-promote"]

RUBRIC_CATEGORIES = frozenset({"fix-now", "outstanding", "marker", "tier-promote"})


@dataclasses.dataclass(frozen=True)
class RouteDecision:
    """Outcome of `rubric.route_after_triggers(...)`.

    `handler` is the symbolic name of the handler module to dispatch to
    (`fix_now`, `outstanding`, `marker_route`, `tier_promote`). Caller
    (`main.py`) imports + invokes the matching module.

    `refused=True` → main.py exits 28; `category` reports the bad input.
    """
    refused: bool
    handler: str  # e.g., "fix_now" / "outstanding" / "marker_route" / "tier_promote"
    category: str
    detail: str = ""


CATEGORY_TO_HANDLER = {
    "fix-now": "fix_now",
    "outstanding": "outstanding",
    "marker": "marker_route",
    "tier-promote": "tier_promote",
}


def route_after_triggers(category: str) -> RouteDecision:
    """Map category enum → handler module. Refuses unknown categories.

    Called only AFTER `triggers.evaluate(...)` returns `forced=False`. The
    `escalate` category is NOT routed here — main.py dispatches it directly
    to escalate.py.
    """
    if category not in RUBRIC_CATEGORIES:
        return RouteDecision(
            refused=True,
            handler="",
            category=category,
            detail=(
                f"category={category!r} is not in the closed rubric "
                f"{sorted(RUBRIC_CATEGORIES)}; per plan §L.4 the four routing "
                f"categories are exhaustive — if none fit, the problem is "
                f"mis-shaped or the agent should re-examine"
            ),
        )
    return RouteDecision(
        refused=False,
        handler=CATEGORY_TO_HANDLER[category],
        category=category,
    )
