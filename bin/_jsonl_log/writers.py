"""Closed-enum constants for the `_orchestrator_log.jsonl` writer module.

Per implplan §C.impl.3 (KNOWN_WRITERS table at v6 — v5 ship + T2
intent_session_auto_register additive bump for `session_start_auto`,
pre-Phase 1 follow-up #1 confirmed applied) and §C.impl.6 (Layer 1 writer
self-declaration).

`KNOWN_WRITERS` is a frozenset; mismatch at write time raises
`UnregisteredWriterError` per §C.impl.5 step 1 (pre-flock, cheap check).

`SUPPORTED_VERSIONS_LOG` enumerates the row-schema versions that the
reader will accept. Process for future additions (§C.impl.3 closing
paragraph):
1. Extend KNOWN_WRITERS with new EMITTED_BY identifier(s).
2. Append the next integer to SUPPORTED_VERSIONS_LOG.
3. Update fixtures + tests.
4. Run §F retry-loop discipline (not a runtime-tunable knob).

The two `bin/chain-overnight` variants, the `--from-develop-plan` variant,
and the colon-suffix sub-emitters are registered as DISTINCT emitters per
plan §C.2 (enables emitter-specific forensics in morning-report queries).
"""

from __future__ import annotations

KNOWN_WRITERS: frozenset[str] = frozenset(
    {
        # §A chain driver
        "bin/chain-overnight",
        "bin/chain-overnight --release-lock",
        "chain_driver_auto",
        # CCOR.1 chain pause/resume CLIs (per design_resolutions
        # R-cli-lint-conformance + T-5/T-6 implementation). The chain-pause
        # CLI emits `chain_paused` event rows; chain-resume emits
        # `chain_resumed` (and the driver's finalizer emits
        # `chain_paused_lock_stale_cleared` + `pause_inject_consumed` via
        # the chain-overnight surface, NOT via these two new emitters).
        "bin/chain-pause",
        "bin/chain-resume",
        # T2 (intent_session_auto_register): stamped by the SessionStart
        # hook's subprocess-call to `bin/intent register --emitted-by
        # session_start_auto` for interactive Claude Code auto-register.
        # KNOWN_WRITERS v5 → v6 additive bump. Must remain consistent with
        # refusal.EMITTED_BY + cli.p_reg choices per research §5.1.
        "session_start_auto",
        # UserPromptSubmit-driven upsert (intent_session_auto_register
        # Part C — forward coverage for sessions renamed after start /
        # never registered at SessionStart).
        "user_prompt_submit_auto",
        # §C recovery (private — only the recovery module is permitted to
        # stamp this; enforced by import-restriction inside writer.py via
        # the call site that always supplies this constant explicitly).
        "_validate_or_truncate_last_line",
        # §E `bin/update_orchestrator`
        "bin/update_orchestrator",
        "bin/update_orchestrator --from-develop-plan",
        # §H `bin/morning-review`
        "bin/morning-review",
        "bin/morning-review:list",
        "bin/morning-review:show",
        "bin/morning-review:reactivate",
        "bin/morning-review:route-outstanding",
        "bin/morning-review:route-marker",
        "bin/morning-review:abandon",
        "bin/morning-review:acknowledge",
        "bin/morning-review:gc",
        "bin/morning-review:index-regen",
        "bin/morning-review:mark-for-eval",
        "bin/morning-review:label-score",
        "bin/morning-review:retire-case",
        # §J eval CLIs
        "bin/eval-gate",
        "bin/eval-trend",
        "bin/eval-baseline",
        "bin/render_spans",
        "bin/regression-replay",
        # §F `bin/verify` (test-step retry loop + phase-boundary review gates)
        # — per implplan §F.impl.3 + §F.impl.7 + §F.impl.8 transition-row
        # emissions. The test-step iteration loop emits per-iter transition
        # rows + halt-handoff deferral rows; the phase-boundary review
        # gates emit per-boundary verdict rows.
        "bin/verify",
        # §K `bin/marker`
        "bin/marker",
        "bin/marker:create",
        "bin/marker:close",
        "bin/marker:validate",
        "bin/marker:register-prefix",
        "bin/marker:route-marker",
        # §L `bin/route_issue` + `bin/lazy-dump-check`
        "bin/route_issue",
        "bin/route_issue:fix-now",
        "bin/route_issue:outstanding",
        "bin/route_issue:marker",
        "bin/route_issue:tier-promote",
        "bin/route_issue:escalate",
        "bin/lazy-dump-check",
        # §M `bin/lessons`
        "bin/lessons",
        "bin/lessons:add",
        "bin/lessons:query",
        "bin/lessons:list",
        # §N `bin/cli-lint`
        "bin/cli-lint",
        "bin/cli-lint:check",
        "bin/cli-lint:list-rules",
        # §P `bin/intent`
        "bin/intent",
        "bin/intent:check",
        "bin/intent:register",
        "bin/intent:update",
        "bin/intent:complete",
        "bin/intent:list",
        "bin/intent:pivot",
        "bin/intent:doctor",
        # §G hook + dispatch CLIs (per G.impl.10 + G.impl.11 + G.impl.12)
        # — bin/log uses KNOWN_WRITERS as the emitter allowlist (per
        # G.impl.11 "exact-match against §C.impl.3 enum"). bin/hook-log
        # itself does NOT enforce against KNOWN_WRITERS at runtime (its
        # `hook` slot is for `.claude/hooks/*` script names, not §C
        # writers); listing it here is for forensic-grep over the cli
        # log surface. bin/security-dispatch + bin/hook-lint may emit
        # rows via bin/log for chain-context observability.
        "bin/hook-log",
        "bin/log",
        "bin/security-dispatch",
        "bin/hook-lint",
        "bin/hook-lint:check",
        "bin/hook-lint:list-rules",
        # Inherited from prior v1 — pre-v1.3
        "bin/ralph-check",
    }
)


# Schema-version trajectory:
# v1 — v1.0-v1.2 original ship
# v2 — v1.3-revised additive bump (§K colon-suffix + new §H + §L)
# v3 — v1.4-revised additive bump (§J eval-* family, §M lessons, §N cli-lint, 3 morning-review sub-emitters)
# v4 — v1.5-audit-response additive bump (§P bin/intent + 7 sub-emitters + chain_driver_auto)
# v5 — §G.impl ship additive bump (bin/hook-log, bin/log, bin/security-dispatch,
#      bin/hook-lint + 2 sub-emitters) — Phase 2 final section.
# v6 — T2 (intent_session_auto_register) additive bump for `session_start_auto`
#      emitted by the SessionStart hook's auto-register subprocess. Threads
#      atomically through refusal.EMITTED_BY + cli.p_reg --emitted-by choices.
SUPPORTED_VERSIONS_LOG: list[int] = [1, 2, 3, 4, 5, 6]


SEVEN_STATUS: frozenset[str] = frozenset(
    {"ready", "wip", "done", "deferred", "blocked", "cancelled", "unknown"}
)
"""7-status enum per plan §1.E. Validated at write time on `transition.from`
and `transition.to` (implplan §C.impl.5 step 1, pre-flock)."""


RECOVERY_EMITTED_BY: str = "_validate_or_truncate_last_line"
"""Lone non-CLI emitter. Only `bin/_jsonl_log/recovery.py` is permitted to
stamp this value per §C.impl.3 closing line."""
