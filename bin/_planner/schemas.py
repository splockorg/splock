"""Schema re-exports + subagent roster source-of-truth.

Per implplan §D.impl.5 schema-embedding strategy. The schemas themselves
are owned by §B.impl (`schemas/plan_v1.schema.json` +
`schemas/orchestrator_v1.schema.json`). This module loads them at import
time so `two_call.py` can pass the dict verbatim as the SDK
`response_format.schema` payload.

Forward-compat refusal is delegated to §B.impl's `schema_registry.
resolve_schema(...)` — if the SDK's structured-output emission fails
because the schema fragment is unsupported at the SDK end (e.g., oneOf
nesting depth limit), the SDK returns
`error_max_structured_output_retries` and the driver halts with exit
code 16 per §D.impl.3. NO downgrade attempt — plan §B.3a explicitly
forbids the fallback path (Finding 20: schema-version refusal must be
loud, not silent).

The subagent roster is loaded from `.claude/agents/_roster.json` —
implplan §D.impl.4 designates this as the closed-enum source for the
`agent_assignment.subagent` field in `orchestrator_v1.schema.json`. The
schema does NOT duplicate the enum (avoids drift); §A.impl + §E.impl
import `SUBAGENT_ROSTER` from here when validating task assignments.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from bin._render_plan.schema_registry import resolve_schema

# Repo-root-anchored path to the agent roster.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ROSTER_PATH = _REPO_ROOT / ".claude" / "agents" / "_roster.json"


def _load_roster() -> tuple[str, ...]:
    """Load and validate the subagent roster from .claude/agents/_roster.json.

    The file is the source-of-truth enum for orchestrator_v1.schema.json's
    agent_assignment.subagent field. Loaded at module import time.

    Raises:
        FileNotFoundError: if `_roster.json` is missing (sealed-state path
            per implplan cross-cutting line 258; only hand-authored edits
            should land it back).
        ValueError: if the JSON structure is unexpected.
    """
    with _ROSTER_PATH.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError(
            f"_roster.json must be a JSON object; got {type(payload).__name__}"
        )
    subagents = payload.get("subagents")
    if not isinstance(subagents, list) or not all(
        isinstance(s, str) for s in subagents
    ):
        raise ValueError(
            "_roster.json must contain a 'subagents' field of type list[str]"
        )
    return tuple(subagents)


# Public re-exports.
PLAN_SCHEMA_V1: Final[dict] = resolve_schema("plan", 1)
"""Schema dict for `<slug>_plan.json` — passed verbatim as Call 2
`response_format.schema` payload when `step == 'plan'`."""

IMPLPLAN_SCHEMA_V1: Final[dict] = resolve_schema("orchestrator", 1)
"""Schema dict for `<slug>_orchestrator.json` — passed verbatim as Call 2
`response_format.schema` payload when `step == 'implplan'`. (Note: plan
§D calls this the 'implplan' step; the schema file is named
`orchestrator_v1` because §B's substrate uses the canonical filename
convention `<slug>_orchestrator.{json,md}` per cross-cutting line 236.)"""

PLAN_PATCH_SCHEMA_V1: Final[dict] = resolve_schema("plan_patch", 1)
"""Schema dict for the surgical-amend patch object (plan_surgical_amend
SC1, task T1). Passed verbatim as the Call 2 `output_config.format.schema`
payload when the planner runs in `--amend` mode (the third schema-selection
branch lands in T6d). Unlike PLAN_SCHEMA_V1 / IMPLPLAN_SCHEMA_V1, Call 2
under this schema emits a KEYED OP-LIST against the prior plan rather than
a full plan; `bin/_planner/patch_apply.py` (T2/T3) applies it. STRICT
(`additionalProperties:false`) so an unknown patch field is rejected, not
silently dropped."""

SUBAGENT_ROSTER: Final[tuple[str, ...]] = _load_roster()
"""Closed-enum tuple of subagent role names. Used by §B.impl.3 (schema
validation of `agent_assignment.subagent` field), §A.impl (spawn-time
dispatch), and §D.impl (this module's own pre-flight sanity check)."""


__all__ = [
    "PLAN_SCHEMA_V1",
    "IMPLPLAN_SCHEMA_V1",
    "PLAN_PATCH_SCHEMA_V1",
    "SUBAGENT_ROSTER",
]
