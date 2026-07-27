---
description: Run the test-step retry loop against a slug's declared test mechanisms via bin/verify
argument-hint: <slug>
---

# /test — operator-direct test-step entry

Triggered by the operator with: `/test $ARGUMENTS`

Where `$ARGUMENTS` is the plan slug.

This command runs `bin/verify test-step <slug>` — the bounded retry
loop per splock §F.3. It is NOT a subagent spawn; the
retry-loop substrate (`bin/_retry_loop/`) internally invokes the
`reviewer` subagent for R1-R5 verdicts per iteration. The slash
command's only job is to dispatch the bash call with the right args.

## File-existence gate

Per v2.7 §1.C, REFUSE if:

- `docs/plans/<slug>/<slug>_orchestrator.json` does NOT exist (run
  `/plan` then `/implplan` first).

Check via Bash before invoking `bin/verify`.

## What to do

1. Parse `$ARGUMENTS` as the slug (single token).
2. Run the gate check. On refusal, print the missing artifact and exit.
3. Generate a synthetic chain-id: `manual_$(date +%Y%m%d_%H%M%S)`.
   (`bin/verify test-step` requires `--chain-id`; for operator-direct
   invocation, the synthetic id namespace-separates manual runs from
   chain-driver runs in the morning-review entries.)
4. Invoke via Bash:
   ```bash
   bin/verify test-step <slug> --chain-id manual_<ts>
   ```
5. Stream output verbatim to the operator. Report the exit code at
   the end.

## Exit codes (passed through from bin/verify)

Per `bin/_retry_loop/exit_codes.py`:

- 0  = success (READY verdict from the iteration loop)
- 1  = usage error
- 2  = driver crash
- 5  = unsupported_schema_version
- 7  = atomic_write_failed (morning-review append failed)
- 10 = phase_boundary_halt (not expected from test-step; reserved)
- 16 = verify_plan_rejected (SDK Structured-Output decode failure)
- 17 = retry_exceeded (unified counter exhausted OR R4 tampering)

Surface the exit code to the operator so they know which halt family
fired without parsing morning-review.

## Side effects to be aware of

Persistence is HALT-PATH ONLY — a green run (exit 0) writes no
morning-review entry, so the absence of one is not evidence the run
never executed. Where to look, by outcome:

- **Green run (exit 0):** the durable receipts are the per-iteration
  verify-output captures `docs/plans/<slug>/_test_output_iter<n>.txt`
  (best-effort, written green or red) and — when the project has opted
  into fleet tracking — the `_fleet_log.jsonl` rows. The reviewer's
  per-iteration R1-R5 verdicts are NOT retained on green.
- **Halt (exit 17):** appends a morning-review entry under
  `docs/plans/<slug>/morning-review/` per §F append-only discipline,
  with the full per-iteration R1-R5 record embedded. Operator-direct
  runs (chain-id starts with `manual_`) are logged the same way.
- Nothing on this path writes the §A.impl.7 `verification/` artifact
  scheme (green or halt) — do not look for it.

## Fleet auto-tracking (opt-in)

No command-level calls needed: when the project has opted into the
fleet lifecycle tracker (`docs/plans/_fleet/_fleet_meta.json`
exists — see `docs/FLEET.md`), `bin/verify test-step` records
`test` / `✈️ wip` on start, `🕛 ready --next /review` on a green run,
and `❌ blocked` on a verdict-carrying halt (retry cap / HALT)
engine-side. On a project that has not opted in this is a no-op.

## Cross-references

- `bin/verify` — POSIX wrapper
- `bin/_retry_loop/main.py` — Python CLI entry
- `.claude/agents/reviewer.md` — Sonnet R1-R5 reviewer contract (invoked
  internally by the retry loop)
- `.claude/agents/verifier.md` — Haiku Ralph completion-gate (invoked by
  the coder, not by /test directly)
- v2.7 §1.C — /test spec
- v2.7 §F.3 — test-step retry loop architecture
- implplan §F.impl.2 — bin/verify CLI surface
