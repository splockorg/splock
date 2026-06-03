"""bin/_retry_loop — §F.impl test-step retry loop + phase-boundary review gates.

Per splock implplan §F.impl (lines 3129-3700) + plan §F + plan §F.9.

This package implements the RUNTIME machinery that fires inside
`bin/chain-overnight` chains:

- **Test-step retry loop** (§F.3 + §F.impl.3): per-iteration Opus attempt
  → bin/verify → Sonnet review subagent dispatch, bounded by the
  unified retry counter and pre-Sonnet budget floor.
- **Phase-boundary review gates** (§F.9 + §F.impl.8): runtime
  plan→implplan and implplan→code reviewer dispatch with the same
  constrained-rubric machinery — answer shape READY / NEEDS_REVISION /
  HALT drives the chain driver's next move.

These are RUNTIME gates inside `bin/chain-overnight`, distinct from the
build-time orchestrator Sonnet review junctions in
`docs/plans/splock/splock_orchestrator.md §5` that
built this very substrate. See orchestrator anchor §4a.3 for the
disambiguation rule that load-bearingly separates the two.

Public surfaces:

- `main` — CLI entry consumed by `bin/verify` and `bin/build_briefing`
  plus the chain driver via `subprocess.Popen([...,
  "-m", "bin._retry_loop.main", ...])`.
- `iteration_loop.run_iteration` — the inner per-iteration orchestrator
  (test-step path); returns an `IterationResult` enum.
- `phase_boundary_review.run_boundary_review` — the runtime §F.9 gate
  orchestrator; returns one of the three terminal verdicts.
- `rubric` — schemas + enum constants (TEST_STEP_RUBRIC_SCHEMA_V1,
  PLAN_TO_IMPLPLAN_RUBRIC_SCHEMA_V1, IMPLPLAN_TO_CODE_RUBRIC_SCHEMA_V1).
- `briefing.build_briefing` — deterministic CLI-driven prompt
  construction per §F.impl.6 + plan §F.9.2.
- `halt_handoff.write_halt_entry` — morning-review handoff per §F.6 +
  §F.9.5.

Consumers:

- `bin._chain_overnight.phase_spawn.spawn_retry_loop_phase` —
  subprocess to `python -m bin._retry_loop.main` for phase 4 (/code)
  and phase 5 (/test).
- `bin._chain_overnight.state_machine` — phase-boundary dispatch
  (between phase=2 and phase=3, then between phase=3 and phase=4).
"""

# Re-exports for in-process consumers (T7 of verifier_sdk_wiring).
# T8 reworks `phase_spawn.spawn_retry_loop_phase` to call
# `run_test_step_loop` directly instead of via subprocess; the
# canonical import path becomes `from bin._retry_loop import ...`
# rather than reaching into the iteration_loop submodule.
from .iteration_loop import (  # noqa: E402,F401 — re-export
    IterationContext,
    IterationRecord,
    IterationResult,
    run_iteration,
    run_test_step_loop,
)

__all__ = [
    "IterationContext",
    "IterationRecord",
    "IterationResult",
    "run_iteration",
    "run_test_step_loop",
]
