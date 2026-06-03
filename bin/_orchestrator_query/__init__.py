"""bin/_orchestrator_query — auto-pick next-ready task from `<slug>_orchestrator.json`.

Per `docs/plans/code_next_ready_pick/` (Tier-2 Phase-3 plan). The CLI in
`main.py` is the substrate for `bin/orchestrator-next-ready`, invoked by
`.claude/commands/code.md` when the operator omits `<task-id>`. The
library function `query.compute_ready_set` is the pure-functional core
the CLI orchestrates — usable as `from bin._orchestrator_query.query
import compute_ready_set` from any future in-process Python caller.

Mirrors the `render_invoker` precedent (orch_status_render T4): pure
library function + thin CLI wrapper, no I/O in the library, all
filesystem traffic + exit-code dispatch in `main.py`.
"""
