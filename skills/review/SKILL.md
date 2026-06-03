---
name: review
description: Run a phase-boundary review gate (plan_to_implplan or implplan_to_code) via bin/verify. Use when the user says "review the phase boundary", "run the review gate for X", or when an orchestrator junction of kind review_gate must be cleared before the DAG advances to the next phase.
---

# review

Phase-boundary review gate. Runs `bin/verify` for a named junction.

Operator entry: `/review <slug> <junction>`, where `<junction>` is a
phase-boundary id (e.g. `plan_to_implplan`, `implplan_to_code`).

Spawns the `reviewer` subagent (`agents/reviewer.md`) — the evaluator-optimizer
junction reviewer that decides whether the boundary is cleared.
