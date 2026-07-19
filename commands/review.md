---
description: Run the phase-boundary review gate (plan_to_implplan or implplan_to_code) via bin/verify
argument-hint: <slug> <junction>
---

# /review — operator-direct phase-boundary review entry

Triggered by the operator with: `/review $ARGUMENTS`

Where `$ARGUMENTS` is `<slug> <junction>` (two tokens, space-separated).

`<junction>` is one of:

- `plan_to_implplan` — review the transition from `<slug>_plan.json` to
  `<slug>_orchestrator.json` (i.e., did `/implplan` faithfully translate
  the plan into orchestrator tasks?)
- `implplan_to_code` — review the transition from orchestrator tasks to
  shipped code (i.e., did `/code` + `/test` deliver the orchestrated
  scope without skipping tasks?)

This command runs `bin/verify boundary <slug> --boundary <junction>` —
the runtime §F.9 phase-boundary review gate. The substrate builds the
deterministic rubric via `bin/_retry_loop/briefing.build_briefing`
(anchor §4a.3 element 3 — NEVER agent-authored), then spawns the
`reviewer` subagent to emit a structured-output verdict.

Note: `test_step` is NOT a valid `<junction>` for `/review` —
test-step reviews fire automatically inside `/test`'s retry loop. The
two boundary junctions (`plan_to_implplan`, `implplan_to_code`) are the
only operator-callable phase-boundary review surfaces.

## File-existence + argument gate

REFUSE if:

- `$ARGUMENTS` is NOT exactly two tokens (slug + junction).
- `<junction>` ∉ `{plan_to_implplan, implplan_to_code}`. Print the
  closed enum so the operator can correct.
- `docs/plans/<slug>/<slug>_orchestrator.json` does NOT exist.
- For `<junction> == plan_to_implplan`: also require
  `<slug>_plan.json` to exist (it's the predecessor).

Check via Bash before invoking.

## What to do

1. Parse `$ARGUMENTS` as exactly two tokens.
2. Run the gate checks. On refusal, print the failing condition and exit.
3. Generate a synthetic chain-id: `manual_$(date +%Y%m%d_%H%M%S)`.
4. Invoke via Bash:
   ```bash
   bin/verify boundary <slug> --chain-id manual_<ts> --boundary <junction>
   ```
5. The substrate builds the briefing deterministically, spawns the
   reviewer subagent with that rubric, and parses the structured-output
   verdict. Stream output to the operator. Report the exit code at end.

## Exit codes (passed through from bin/verify)

- 0  = success: verdict is READY or NEEDS_REVISION (the chain driver
       distinguishes; for operator-direct, both exit 0)
- 1  = usage error
- 10 = `phase_boundary_halt` (HALT verdict from the reviewer)
- 16 = `verify_plan_rejected` (SDK Structured-Output decode failure)
- 17 = `retry_exceeded` / R4 tampering

## Side effects

- Reviewer verdict logged under `verification/` per §A.impl.7.
- Morning-review entry appended on halt verdicts.

## Fleet auto-tracking (opt-in)

No command-level calls needed: when the project has opted into the
fleet lifecycle tracker (`docs/plans/_fleet/_fleet_meta.json`
exists — see `docs/FLEET.md`), `bin/verify boundary` records
`review` / `✈️ wip` on start, `🕛 ready` with the junction's next
command on READY (`plan_to_implplan` → `/implplan`,
`implplan_to_code` → `/code`), and `❌ blocked` on a HALT verdict
engine-side. On a project that has not opted in this is a no-op.

## Cross-references

- `bin/verify` — POSIX wrapper (`boundary` subcommand)
- `bin/_retry_loop/phase_boundary_review.py` — runtime gate substrate
- `bin/_retry_loop/briefing.py` — deterministic rubric construction
- `.claude/agents/reviewer.md` — reviewer subagent contract
- v2.7 §1.C — /review spec
- v2.7 §F.9 — phase-boundary review architecture
- research_findings_v1.md §E — narrative-driven-verifier anti-pattern
  (why the rubric MUST be deterministic)
