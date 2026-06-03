---
name: implplan
description: Promote a plan substrate into an executable orchestrator substrate (tasks DAG, junctions, per-task test wiring) via the two-call planner. Use when the user says "implplan X", "turn the plan into tasks", "build the orchestrator for X", or after /plan when a slug is ready to be decomposed into ordered, dependency-gated tasks. Emits <slug>_orchestrator.json and renders the .md twin.
---

# implplan

Promotes `<slug>_plan.json` into `<slug>_orchestrator.json` — the executable
task DAG with `depends_on` edges, junction gates, and per-task `tests_enabled`.

Operator entry: `/implplan <slug> [free-text]`.

Routes through `bin/implplan` -> `python -m bin._planner.main implplan`,
constrained to `schemas/orchestrator_v1.schema.json` (whose
`agent_assignment.subagent` enum is sourced from `agents/_roster.json`).

The emitted `<slug>_orchestrator.json` is sealed substrate.
