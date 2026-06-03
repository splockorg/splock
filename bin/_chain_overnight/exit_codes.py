"""Closed-enum exit codes for `bin/chain-overnight`.

Per implplan §A.impl.3a (cross-CLI shared exit-code registry, lines
452-528). The chain driver consumes exit codes from the binaries it
orchestrates (`bin/plan`, `bin/implplan`, `bin/verify_plan`,
`bin/_retry_loop/main.py`, etc.) AND emits its own codes for halts that
originate in the driver layer.

Discipline:
- Numeric assignments here MUST match A.impl.3a verbatim. Code 9
  (`sealed_path_refused`) is §A-owned. Code 7 / 10 / 16 / 17 are
  shared and propagated from §B / §D / §F.
- Code 20 / 21 are §A-owned (concurrent-chain refusal + foreign-sentinel
  detect) per A.impl.4 v1.2.
- Code 18 is §A-owned for operator-kill (per A.impl.10 #2 RATIFIED
  2026-05-21; mechanism in §A.5 v1.2 Hole H.4 resolution).

The chain driver does NOT invent new codes; if a downstream binary
returns an unfamiliar code, the driver wraps it in code 10
(`phase_boundary_halt`) and surfaces the underlying code in the
completion summary's `## Chain execution` section per plan §A.5a.
"""

from __future__ import annotations

# Universal
EXIT_OK = 0
"""Chain completed all phases successfully (per plan §A.4)."""

EXIT_DRIVER_CRASH = 2
"""Driver itself crashed (script bug, missing dependency, unreadable
plan dir). Distinct from chain halts; never produced by a step agent
(per plan §A.4 last row)."""

EXIT_ATOMIC_WRITE_FAILED = 7
"""Atomic temp + rename failed during a driver-side write (manifest,
sentinel, completion summary). Shared with §B per A.impl.3a."""

EXIT_SEALED_PATH_REFUSED = 9
"""Pre-stage safety net refused a `git add` — credential-shaped or
sealed-state path detected. §A-owned per A.impl.3a + A.impl.6."""

EXIT_PHASE_BOUNDARY_HALT = 10
"""Chain halted at a phase boundary per the per-boundary failure-mode
table (plan §G.7). Not an error per se; operator review required.
Mapped from underlying binary exit codes the driver does not recognize
in the registry."""

EXIT_WALL_CLOCK_CAP = 11
"""Wall-clock cap (`OVERNIGHT_WALL_CLOCK_SECONDS`, default 28800)
fired. Resumable via `--from-resume`."""

EXIT_GUARDRAIL_REFUSED = 14
"""GUARDRAIL refused a chain action; operator must adjust before resume
(per plan §A.4)."""

EXIT_ORPHAN_DETECTED = 15
"""Orphan-state detected at pre-spawn scan; operator must triage before
resume (per plan §A.4 / §G.9)."""

EXIT_VERIFY_PLAN_REJECTED = 16
"""§B's `bin/verify_plan` rejected the plan emission; OR §D's SDK
retry exhaustion. Shared schema-related halt family per A.impl.3a."""

EXIT_RETRY_EXCEEDED = 17
"""Test-step retry loop exhausted; tests still failing; operator
handoff. Shared with §F per A.impl.3a."""

EXIT_OPERATOR_KILLED = 18
"""Chain killed via `bin/chain-overnight --kill <chain_id>` per
A.impl.10 #2 RATIFIED 2026-05-21 + plan §Pillar 3 H.4. Stop hook
forensic-trail emits before SIGKILL (10s grace default).

Note: code 18 is multi-owned across the registry — §E also uses 18 for
`task_outside_develop_plan_authority` (per A.impl.3a v1.3). The chain
driver only ever emits 18 for operator-kill; §E only emits 18 for
develop-plan authority refusal. Scope disambiguated by the calling
binary, per the v1.3-revised propagation discipline."""

EXIT_CHAIN_REFUSED = 20
"""Concurrent-chain refused via H.9 1C sentinel (`_chain_running.lock`
exists for a live chain). §A-owned per A.impl.3a + A.impl.4 v1.2."""

EXIT_CHAIN_FOREIGN_SENTINEL = 21
"""Mid-chain foreign-sentinel detection at every STARTING→RUNNING edge.
Defense-in-depth against race conditions during chain spawn. §A-owned
per A.impl.3a + A.impl.4 v1.2."""

EXIT_NOT_PAUSED = 22
"""`bin/chain-resume` invoked when no pause sentinel exists, OR the chain
is in orphan-paused state (sentinel present + driver dead/missing).
CCOR.1-owned per R-exit-codes + R-orphan-detection. Allocated in T-5
(this CLI) alongside `EXIT_ALREADY_PAUSED = 23` to avoid a 2-PR split;
T-6's `bin/chain-resume` is the actual emitter."""

EXIT_ALREADY_PAUSED = 23
"""`bin/chain-pause` invoked when the pause sentinel already exists for
the live chain. Maps from `pause_sentinel.PauseAlreadyHeldError` at the
CLI layer. CCOR.1-owned per R-exit-codes; non-idempotent by design —
the second pause is an informative refusal, not silent noop."""


# Aggregate set for caller-side membership checks. Codes here are the
# §A-emit slot in the cross-CLI shared registry — codes that the chain
# driver itself raises (vs. propagates verbatim from a downstream call).
#
# Note: `EXIT_NOT_PAUSED` (22) and `EXIT_ALREADY_PAUSED` (23) are emitted
# by the `bin/chain-pause` + `bin/chain-resume` CLIs respectively, NOT by
# the chain driver itself. They are intentionally excluded from
# `DRIVER_EMITTED_CODES` per the v1 convention (this set tracks codes the
# driver raises directly; CLI-emitted codes live alongside but are
# scope-disambiguated by the calling binary, per A.impl.3a v1.3).
DRIVER_EMITTED_CODES = frozenset(
    {
        EXIT_OK,
        EXIT_DRIVER_CRASH,
        EXIT_ATOMIC_WRITE_FAILED,
        EXIT_SEALED_PATH_REFUSED,
        EXIT_PHASE_BOUNDARY_HALT,
        EXIT_WALL_CLOCK_CAP,
        EXIT_GUARDRAIL_REFUSED,
        EXIT_ORPHAN_DETECTED,
        EXIT_VERIFY_PLAN_REJECTED,
        EXIT_RETRY_EXCEEDED,
        EXIT_OPERATOR_KILLED,
        EXIT_CHAIN_REFUSED,
        EXIT_CHAIN_FOREIGN_SENTINEL,
    }
)


# CCOR.1 chain-pause / chain-resume CLI emitters — codes 22 + 23 are
# emitted by those binaries, not by the chain driver. Listed here for
# closed-enum cross-reference (analogue to `PROPAGATED_FROM_*` tables).
CHAIN_PAUSE_EMITTED_CODES = frozenset(
    {
        EXIT_DRIVER_CRASH,        # plan-dir absent, no running lock, dead PID, strict-args
        EXIT_ALREADY_PAUSED,      # sentinel already held
    }
)

CHAIN_RESUME_EMITTED_CODES = frozenset(
    {
        EXIT_DRIVER_CRASH,        # plan-dir absent, inject errors, chain_id mismatch
        EXIT_NOT_PAUSED,          # sentinel absent OR orphan-paused
    }
)


# Mapping from underlying-binary exit codes the driver propagates
# (post §D's PlannerEmissionExhausted catch).
# This is the §D→§A bridge table: §D emits 7 and 16; the chain driver
# preserves those codes verbatim per A.impl.3a "scope of the shared
# registry".
PROPAGATED_FROM_PLANNER = {
    7: EXIT_ATOMIC_WRITE_FAILED,   # planner CLI atomic-write failure
    16: EXIT_VERIFY_PLAN_REJECTED,  # PlannerEmissionExhausted
}


# Mapping from underlying-binary exit codes for §B's `bin/verify_plan`.
PROPAGATED_FROM_VERIFY_PLAN = {
    2: EXIT_PHASE_BOUNDARY_HALT,           # EXIT_PLAN_NOT_FOUND — wrap
    3: EXIT_VERIFY_PLAN_REJECTED,          # EXIT_JSON_MALFORMED
    4: EXIT_VERIFY_PLAN_REJECTED,          # EXIT_SCHEMA_REJECTED
    5: EXIT_VERIFY_PLAN_REJECTED,          # EXIT_UNSUPPORTED_SCHEMA_VERSION
    6: EXIT_VERIFY_PLAN_REJECTED,          # EXIT_TEMPLATE_ERROR
    7: EXIT_ATOMIC_WRITE_FAILED,           # EXIT_ATOMIC_WRITE_FAILED
    11: EXIT_VERIFY_PLAN_REJECTED,         # EXIT_DRIFT
}
