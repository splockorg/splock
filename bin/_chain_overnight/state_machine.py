"""Chain driver state machine + §C transition-log emitter.

Per implplan §A.impl.4 (lines 530-560) + plan §A.5 four-state diagram.

States (closed enum):
    STARTING          → STARTING_PHASE_N (N=2..5)
    STARTING_PHASE_N  → RUNNING_PHASE_N  (sentinel re-check + pre-spawn)
    RUNNING_PHASE_N   → EVALUATING_BOUNDARY
    EVALUATING_BOUNDARY → COMMITTING | HALTED
    COMMITTING        → STARTING_PHASE_{N+1} | CHAIN_COMPLETE
    HALTED            (terminal)
    CHAIN_COMPLETE    (terminal)

Every transition writes one row to `_orchestrator_log.jsonl` via §C's
`append_row(..., emitted_by="bin/chain-overnight")`. The 7-status enum
in the orchestrator-log schema constrains transition.from/to to:
`ready` / `wip` / `done` / `deferred` / `blocked` / `cancelled` /
`unknown`. The chain-driver state machine's own states are NOT the
7-status enum — we map them to the closest 7-status equivalent for
each row:

    STARTING               → transition: ready → wip
    SPAWNING_PHASE_N       → transition: wip → wip (loop continuation)
    EVALUATING_BOUNDARY    → transition: wip → wip
    COMMITTING (success)   → transition: wip → done (or wip on intra-chain)
    HALTED                 → transition: wip → blocked (cost/wall halt)
                              OR wip → deferred (deferred-threshold halt)
                              OR wip → cancelled (operator-killed)
    CHAIN_COMPLETE         → transition: wip → done

These mappings preserve a coherent task-level narrative for downstream
morning-review consumers while letting the chain driver's own state
machine be the load-bearing source.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bin._jsonl_log import append_row

from . import exit_codes

logger = logging.getLogger(__name__)


EMITTED_BY_CHAIN_DRIVER = "bin/chain-overnight"
EMITTED_BY_AUTO_REGISTER = "chain_driver_auto"
EMITTED_BY_RELEASE_LOCK = "bin/chain-overnight --release-lock"


# Closed enum of driver-side state names (NOT the 7-status enum;
# tracked separately for forensic-grade per-phase recovery).
DRIVER_STATES = frozenset(
    {
        "STARTING",
        "SPAWNING_PHASE",
        "EVALUATING_BOUNDARY",
        "COMMITTING",
        "CHAIN_COMPLETE",
        "HALTED",
    }
)


@dataclass
class TransitionContext:
    """Bundle of context for `emit_transition(...)`.

    Mirrors the orchestrator_log_v1.schema.json required-field shape so
    the writer never needs to synthesize missing fields.
    """

    plan_dir: Path
    slug: str
    chain_id: str
    session_id: str
    task_id: str | None
    transition_from: str  # 7-status enum
    transition_to: str    # 7-status enum
    reason: str
    overnight: bool = True
    guardrail: bool = False
    emitted_by: str = EMITTED_BY_CHAIN_DRIVER
    pointer: str | None = None
    retry_count: int | None = None


def emit_transition(ctx: TransitionContext) -> None:
    """Write one row to `_orchestrator_log.jsonl` via §C's append_row.

    Per implplan §A.impl.4: every transition emits a row. The chain
    driver uses `bin/chain-overnight` as `emitted_by`; the auto-register
    hook uses `chain_driver_auto`; the `--release-lock` flow uses
    `bin/chain-overnight --release-lock`. All three are in KNOWN_WRITERS
    v4 (per implplan §C.impl.3 + §A.impl.5b cross-reference).
    """
    row = {
        "session_id": ctx.session_id,
        "plan_slug": ctx.slug,
        "chain_id": ctx.chain_id,
        "task_id": ctx.task_id,
        "transition": {"from": ctx.transition_from, "to": ctx.transition_to},
        "pointer": ctx.pointer,
        "retry_count": ctx.retry_count,
        "mode_at_transition": {
            "overnight": ctx.overnight,
            "guardrail": ctx.guardrail,
        },
        "override_in_effect": None,
        "reason": ctx.reason,
        "verifier_verdict_ref": None,
    }
    append_row(ctx.plan_dir, row, emitted_by=ctx.emitted_by)
    logger.debug(
        "transition emitted plan_dir=%s session_id=%s from=%s to=%s emitted_by=%s",
        ctx.plan_dir, ctx.session_id, ctx.transition_from,
        ctx.transition_to, ctx.emitted_by,
    )


# ----------------------------------------------------------------------
# Convenience transition emitters
# ----------------------------------------------------------------------


def emit_chain_start(
    plan_dir: Path,
    *,
    slug: str,
    chain_id: str,
    session_id: str,
    reason: str = "chain driver entered STARTING state",
) -> None:
    """READY → STARTING — initial chain-spawn transition."""
    emit_transition(
        TransitionContext(
            plan_dir=plan_dir,
            slug=slug,
            chain_id=chain_id,
            session_id=session_id,
            task_id=None,
            transition_from="ready",
            transition_to="wip",
            reason=reason,
        )
    )


def emit_phase_boundary(
    plan_dir: Path,
    *,
    slug: str,
    chain_id: str,
    session_id: str,
    phase: int,
    verdict: str,
    reason: str,
    exit_code: int | None = None,
) -> None:
    """SPAWNING_PHASE → EVALUATING_BOUNDARY → next.

    `verdict` maps to a 7-status transition:
    - passed → wip → wip (intra-chain) OR wip → done (final phase)
    - cost_cap_exceeded → wip → blocked
    - wall_clock_exceeded → wip → blocked
    - concurrent_chain_refused → wip → blocked
    - verify_plan_rejected → wip → blocked
    - tests_enabled_rejected → wip → blocked
    - retry_exceeded → wip → deferred
    - phase_boundary_halt → wip → blocked
    - sealed_path_refused → wip → blocked
    - insufficient_budget → wip → blocked
    """
    to_status = _map_verdict_to_status(verdict)
    detail_reason = f"phase={phase} verdict={verdict}; {reason}"
    if exit_code is not None:
        detail_reason += f" exit_code={exit_code}"
    emit_transition(
        TransitionContext(
            plan_dir=plan_dir,
            slug=slug,
            chain_id=chain_id,
            session_id=session_id,
            task_id=None,
            transition_from="wip",
            transition_to=to_status,
            reason=detail_reason,
        )
    )


def emit_chain_complete(
    plan_dir: Path,
    *,
    slug: str,
    chain_id: str,
    session_id: str,
    reason: str = "chain completed all phases successfully",
) -> None:
    """COMMITTING → CHAIN_COMPLETE — terminal success."""
    emit_transition(
        TransitionContext(
            plan_dir=plan_dir,
            slug=slug,
            chain_id=chain_id,
            session_id=session_id,
            task_id=None,
            transition_from="wip",
            transition_to="done",
            reason=reason,
        )
    )


def emit_chain_halted(
    plan_dir: Path,
    *,
    slug: str,
    chain_id: str,
    session_id: str,
    halt_reason: str,
    detail: str = "",
) -> None:
    """SPAWNING_PHASE → HALTED — terminal halt.

    `halt_reason` is the closed-enum plan §A.5a value. Maps to a
    7-status transition per `_map_halt_reason_to_status`.
    """
    to_status = _map_halt_reason_to_status(halt_reason)
    reason_full = f"halt_reason={halt_reason}"
    if detail:
        reason_full += f"; {detail}"
    emit_transition(
        TransitionContext(
            plan_dir=plan_dir,
            slug=slug,
            chain_id=chain_id,
            session_id=session_id,
            task_id=None,
            transition_from="wip",
            transition_to=to_status,
            reason=reason_full,
        )
    )


def emit_sealed_path_refused(
    plan_dir: Path,
    *,
    slug: str,
    chain_id: str,
    session_id: str,
    matched_path: str,
    matched_pattern: str,
) -> None:
    """COMMITTING → HALTED — pre-stage safety net refused.

    Per A.impl.6 — transition.to is "sealed_path_stage_refused" in the
    *narrative* sense, but the schema's 7-status enum maps this to
    `blocked`.
    """
    emit_transition(
        TransitionContext(
            plan_dir=plan_dir,
            slug=slug,
            chain_id=chain_id,
            session_id=session_id,
            task_id=None,
            transition_from="wip",
            transition_to="blocked",
            reason=(
                f"sealed_path_stage_refused: would_stage_sealed_path: "
                f"{matched_path} (pattern={matched_pattern})"
            ),
        )
    )


def emit_release_lock(
    plan_dir: Path,
    *,
    slug: str,
    chain_id: str,
    session_id: str,
    reason: str = "operator invoked --release-lock",
) -> None:
    """`bin/chain-overnight --release-lock` flow logs sentinel-released.

    Per implplan §A.impl.3 lines 433-440. emitted_by is
    `bin/chain-overnight --release-lock` (KNOWN_WRITERS v4 entry).
    """
    emit_transition(
        TransitionContext(
            plan_dir=plan_dir,
            slug=slug,
            chain_id=chain_id,
            session_id=session_id,
            task_id=None,
            transition_from="wip",
            transition_to="cancelled",
            reason=reason,
            emitted_by=EMITTED_BY_RELEASE_LOCK,
        )
    )


def emit_chain_session_auto_registered(
    plan_dir: Path,
    *,
    slug: str,
    chain_id: str,
    session_id: str,
    intent_session_id: str | None,
) -> None:
    """§A.impl.5b step 6 — log the auto-register row.

    `emitted_by="chain_driver_auto"` (KNOWN_WRITERS v4).
    """
    reason = (
        f"chain_session_auto_registered intent_session_id="
        f"{intent_session_id or '(none)'}"
    )
    emit_transition(
        TransitionContext(
            plan_dir=plan_dir,
            slug=slug,
            chain_id=chain_id,
            session_id=session_id,
            task_id=None,
            transition_from="ready",
            transition_to="wip",
            reason=reason,
            emitted_by=EMITTED_BY_AUTO_REGISTER,
        )
    )


# ----------------------------------------------------------------------
# 7-status mapping helpers
# ----------------------------------------------------------------------


_VERDICT_STATUS: dict[str, str] = {
    "passed": "wip",
    "cost_cap_exceeded": "blocked",
    "wall_clock_exceeded": "blocked",
    "concurrent_chain_refused": "blocked",
    "verify_plan_rejected": "blocked",
    "tests_enabled_rejected": "blocked",
    "retry_exceeded": "deferred",
    "phase_boundary_halt": "blocked",
    "sealed_path_refused": "blocked",
    "insufficient_budget": "blocked",
    "atomic_write_failed": "blocked",
    "operator_killed": "cancelled",
}


def _map_verdict_to_status(verdict: str) -> str:
    """Closed-enum mapping; unknowns default to `unknown`."""
    return _VERDICT_STATUS.get(verdict, "unknown")


_HALT_REASON_STATUS: dict[str, str] = {
    "phase_success": "done",
    "retry_exceeded": "deferred",
    "cost_exhausted": "blocked",
    "cost_cap_exceeded": "blocked",
    "wall_clock_exceeded": "blocked",
    "tampering_detected": "blocked",
    "operator_killed": "cancelled",
    "driver_crash": "blocked",
    "plan_schema_rejection": "blocked",
    "verify_plan_rejected": "blocked",
    "tests_enabled_rejected": "blocked",
    "concurrent_chain_refused": "blocked",
    "concurrent_chain_foreign": "blocked",
    "sealed_path_refused": "blocked",
    "insufficient_budget": "blocked",
}


def _map_halt_reason_to_status(halt_reason: str) -> str:
    """Closed-enum mapping; unknowns default to `blocked`."""
    return _HALT_REASON_STATUS.get(halt_reason, "blocked")


def verdict_for_verify_plan_exit(downstream_exit: int) -> str:
    """Map a `bin/verify_plan --strict` downstream exit code to a verdict.

    Per real_tests_at_junctions SC2 (T3): the tests_enabled plan-defect
    code (`exit_codes.EXIT_TESTS_ENABLED_REJECTED` = 44, propagated
    verbatim per `exit_codes.PROPAGATED_FROM_VERIFY_PLAN`) maps to its
    OWN verdict — `tests_enabled_rejected` — instead of collapsing
    silently into the generic `verify_plan_rejected` family the way
    render-plan exits 3/4/5/6/11 do. Both verdicts land on 7-status
    `blocked`; the distinct verdict string is what surfaces the
    "fix the plan authoring" triage signal in the boundary row.

    This is the verdict seam for `phase_spawn.spawn_planner_phase`'s
    verify branch (which today hardcodes `verify_plan_rejected`); the
    chain-side wiring threads the `downstream_exit_code` it already
    captures through this helper.
    """
    if downstream_exit == exit_codes.EXIT_TESTS_ENABLED_REJECTED:
        return "tests_enabled_rejected"
    return "verify_plan_rejected"


__all__ = [
    "DRIVER_STATES",
    "EMITTED_BY_AUTO_REGISTER",
    "EMITTED_BY_CHAIN_DRIVER",
    "EMITTED_BY_RELEASE_LOCK",
    "TransitionContext",
    "emit_chain_complete",
    "emit_chain_halted",
    "emit_chain_session_auto_registered",
    "emit_chain_start",
    "emit_phase_boundary",
    "emit_release_lock",
    "emit_sealed_path_refused",
    "emit_transition",
    "verdict_for_verify_plan_exit",
]
