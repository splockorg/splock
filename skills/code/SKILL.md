---
name: code
description: Execute one orchestrator task under the Ralph completion gate — spawn the coder subagent against <slug>_orchestrator.json tasks[<task-id>] (auto-picks the next-ready task when task-id omitted). Use when the user says "code X", "implement task T-N", "run the next task", "/code X", or to drive the implement+verify loop after /implplan. Writes code, runs tests via bin/verify, refuses completion until the verifier answers READY.
---

# code

Per-task implementation under the Ralph completion gate. Spawns the `coder`
subagent (`agents/coder.md`) against a single task entry in
`<slug>_orchestrator.json`.

Operator entry: `/code <slug> [<task-id>] [-- <directive>]`. With no task-id the
next-ready task (by `depends_on` satisfaction) is auto-picked. An operator
directive after `--` is wrapped via `bin/wrap` before injection.

The coder writes code within the task's `file_paths_touched`, runs the task's
`tests_enabled` through `bin/verify`, and may NOT declare completion until the
`verifier` subagent returns READY. State transitions go through
`bin/update_orchestrator` only (never a hand-edit of `_state.json`).
