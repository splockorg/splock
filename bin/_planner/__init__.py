"""Two-call planner invocation substrate (splock v2.7 §D).

Public surface:
- `invoke_planner(slug, step, inputs, ...)` — the Python callable used by
  the chain driver (§A.impl) for in-process planning phases.
- `PlannerResult` / `PlannerInputs` dataclasses describing the typed I/O.
- `PlannerEmissionExhausted` — raised on SDK retry-exhaustion.
- `PLAN_SCHEMA_V1` / `IMPLPLAN_SCHEMA_V1` — re-exported from §B.impl
  schema registry; consumed at Call 2 as `response_format.schema`.
- `SUBAGENT_ROSTER` — closed-enum source for `agent_assignment.subagent`
  per implplan §B.impl.3 + §D.impl.4.

Subscription-mode pricing (Claude Code Max): no per-call usage cap is
enforced at this layer; max_tokens is the only operator-controlled knob.

The CLI surface (`bin/plan` + `bin/implplan`) lives in `main.py`; thin
POSIX shell wrappers at `bin/plan` / `bin/implplan` activate the venv and
dispatch via `python -m bin._planner.main`.
"""

from __future__ import annotations

from .schemas import (
    IMPLPLAN_SCHEMA_V1,
    PLAN_SCHEMA_V1,
    SUBAGENT_ROSTER,
)
from .two_call import (
    PlannerEmissionExhausted,
    PlannerInputs,
    PlannerResult,
    invoke_planner,
)

__all__ = [
    "invoke_planner",
    "PlannerResult",
    "PlannerInputs",
    "PlannerEmissionExhausted",
    "PLAN_SCHEMA_V1",
    "IMPLPLAN_SCHEMA_V1",
    "SUBAGENT_ROSTER",
]
