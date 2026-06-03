"""Closed-enum exit codes for `bin/orchestrator-next-ready` (code_next_ready_pick T1/T2).

Per the slug's plan §F2 + orchestrator T2 acceptance contract. Codes are
disjoint from `bin/_update_orchestrator/exit_codes.py` and
`bin/_jsonl_log/exit_codes.py` (the latter does not currently exist; the
test allows for absence by skipping the disjoint check against missing
sibling modules) per the cross-CLI shared registry pattern at implplan
§A.impl.3a.

Reserved-gap discipline (plan §F2 / implplan call-1 line 162): codes 1,
3-9, 14-19, 24+ are intentionally unallocated so future bin/ modules can
mint into the local namespace without colliding with this CLI's
contracts.

| Code | Family | Source |
|---|---|---|
| 0 | success | universal — next-ready task printed |
| 2 | usage | argparse / bad CLI surface |
| 10 | slug_not_found | docs/plans/<slug>/ missing |
| 11 | orchestrator_json_missing | plan dir exists, orchestrator JSON missing |
| 12 | orchestrator_json_malformed | unparseable orchestrator JSON |
| 13 | state_shape_invalid | _state.json present but tasks neither dict nor array (post-T0 narrowing per V2/D-shape) |
| 20 | no_ready_task_all_blocked | verdict=all_blocked |
| 21 | no_ready_task_all_wip | verdict=all_wip |
| 22 | no_ready_task_mixed | verdict=mixed_no_ready |
| 23 | plan_complete_all_done | verdict=all_done |
"""

from __future__ import annotations

# Universal success: the picker found a ready task and printed it to stdout.
EXIT_OK = 0

# argparse / bad CLI surface (e.g., missing positional slug, unknown flag).
EXIT_USAGE = 2

# `docs/plans/<slug>/` directory does not exist; slug typo or never-planned.
EXIT_SLUG_NOT_FOUND = 10

# Plan dir exists but `<slug>_orchestrator.json` is missing; planner stage
# incomplete (plan_v1 may exist without orchestrator_v1 if Phase 3 was
# skipped).
EXIT_ORCHESTRATOR_JSON_MISSING = 11

# `<slug>_orchestrator.json` exists but is not parseable JSON, or it parses
# but the `tasks` key is missing or non-array (schema-shape rejection at
# the read seam — full schema validation is the chain driver's job, not
# the picker's).
EXIT_ORCHESTRATOR_JSON_MALFORMED = 12

# `_state.json` exists but its `tasks` field is neither a dict nor an
# array (e.g., string/int/null). Per T0 V2/D-shape branch decision:
# dict shape is normalized, not refused; only genuinely malformed
# shapes exit 13.
EXIT_STATE_SHAPE_INVALID = 13

# No task is ready; ≥1 task is blocked; no task is wip. Operator must
# unblock or cancel a blocker before another `/code` invocation will
# pick something.
EXIT_NO_READY_TASK_ALL_BLOCKED = 20

# No task is ready; ≥1 task is wip; no task is blocked. A `wip` task
# already owns the slot — operator should resume rather than auto-pick.
EXIT_NO_READY_TASK_ALL_WIP = 21

# No task is ready; mixed terminal states present (some done/deferred/
# cancelled alongside blocked/wip) with no `ready` slot to take.
EXIT_NO_READY_TASK_MIXED = 22

# Every task is in a terminal-done-equivalent status
# (`done`/`cancelled`/`deferred`). Plan is finished; nothing to pick.
EXIT_PLAN_COMPLETE_ALL_DONE = 23


ALL_CODES = frozenset(
    {
        EXIT_OK,
        EXIT_USAGE,
        EXIT_SLUG_NOT_FOUND,
        EXIT_ORCHESTRATOR_JSON_MISSING,
        EXIT_ORCHESTRATOR_JSON_MALFORMED,
        EXIT_STATE_SHAPE_INVALID,
        EXIT_NO_READY_TASK_ALL_BLOCKED,
        EXIT_NO_READY_TASK_ALL_WIP,
        EXIT_NO_READY_TASK_MIXED,
        EXIT_PLAN_COMPLETE_ALL_DONE,
    }
)
