---
name: plan
description: Author a structured plan for a slug via the two-call planner (Call 1 free-form reasoning, Call 2 schema-valid JSON emission). Use when the user says "plan X", "write a plan for X", "design the approach to X", or after recon when an initiative needs success criteria + a task skeleton. Emits <slug>_plan.json and renders the <slug>_plan.md twin.
---

# plan

Two-call planning engine. Produces a schema-valid plan substrate
(`docs/plans/<slug>/<slug>_plan.json`) and its rendered Markdown twin
(`<slug>_plan.md`).

Operator entry: `/plan <slug> [free-text]`. A bare slug is a fresh plan;
additional tokens carry a directive or a `--reopen`/`--amend` mode.

Routes through `bin/plan` -> `python -m bin._planner.main plan`, which makes two
distinct model calls: Call 1 is free-form reasoning, Call 2 is constrained to
`schemas/plan_v1.schema.json`. The `planner` subagent (`agents/planner.md`) is
the model-side contract; the driver makes the calls.

The emitted `<slug>_plan.json` is sealed substrate — surgical edits go through
`bin/plan --amend`, never a raw file edit.
