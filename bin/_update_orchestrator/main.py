"""CLI entry point for `bin/update_orchestrator` (implplan §E.impl.3).

Two invocation modes:

1. Base CLI form (positional status):
       bin/update_orchestrator <slug> <task_id> <canonical_status> \
           [--pointer <pointer>] [--reason <text>]
   Stamps `emitted_by: "bin/update_orchestrator"`.

2. `--from-develop-plan` subcommand mode:
       bin/update_orchestrator --from-develop-plan <slug> <task_id> <native_status>
   Stamps `emitted_by: "bin/update_orchestrator --from-develop-plan"`.

Reverse mapping is NOT supported (per plan §E.3); `--to-develop-plan` is
explicitly rejected as bad CLI surface (exit 1).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional

from bin._env_paths import project_root
from bin._fleet import auto as fleet_auto

from .canonical_transitions import (
    VERDICT_ALLOW,
    VERDICT_REFUSE_DONE_WIP_NO_OVERRIDE,
    VERDICT_REFUSE_UNKNOWN_STATUS,
    validate_transition,
)
from .exit_codes import (
    EXIT_DONE_WIP_ROLLBACK_REFUSED,
    EXIT_DUAL_RETRY_CAP_MUTEX_VIOLATED,
    EXIT_ITERATION_OVERFLOW_REFUSED,
    EXIT_OK,
    EXIT_PHASE_BOUNDARY_HALT,
    EXIT_SCHEMA_REJECTED,
    EXIT_TASK_OUTSIDE_DEVELOP_PLAN_AUTHORITY,
    EXIT_UNSUPPORTED_SCHEMA_VERSION,
    EXIT_USAGE,
)
from .from_develop_plan import (
    NativeStatusParseError,
    SIDE_EFFECT_APPEND_ITERATION,
    TaskOutsideAuthorityError,
    ensure_task_in_authority,
    map_develop_plan_to_canonical,
)
from .log_emit import (
    EMIT_BASE,
    EMIT_FROM_DEVELOP_PLAN,
    emit_state_md_render_failed,
    emit_state_md_rendered,
    emit_transition,
)
from .refusal import (
    emit_refusal,
    refusal_done_wip_rollback,
    refusal_dual_retry_cap_mutex,
    refusal_iteration_overflow,
    refusal_schema_rejected,
    refusal_task_outside_develop_plan_authority,
    refusal_unsupported_schema_version,
)
from .state_writer import (
    get_task_entry,
    get_task_status,
    read_state,
    set_task_status,
    state_flock,
    write_state,
)
from .telemetry_schema import (
    DualRetryCapMutexViolation,
    IterationOverflowError,
    SUPPORTED_VERSIONS_TELEMETRY,
    TelemetrySchemaError,
    TelemetryUnsupportedVersionError,
    append_iteration_row,
    check_dual_retry_cap_mutex,
)


def _repo_root() -> Path:
    """Resolve the adopter project root, honouring ``$CLAUDE_PROJECT_DIR``.

    Delegates to ``bin._env_paths.project_root()`` so state flips land in the
    ADOPTER's ``docs/plans/`` (not the plugin install tree) when splock runs
    as an installed plugin — same fix class as the picker (fork finding F2);
    this CLI is the write half of every ``/code`` status transition.
    """
    return project_root()


def _plan_dir(slug: str, repo_root: Optional[Path] = None) -> Path:
    root = repo_root or _repo_root()
    return root / "docs" / "plans" / slug


def _is_chain_context() -> bool:
    """True iff `SPLOCK_CHAIN_ID` env var is set (driver propagates per
    A.impl.5a + §I.impl.3 driver-set-chain-context class)."""
    return bool(os.environ.get("SPLOCK_CHAIN_ID"))


def _chain_id() -> Optional[str]:
    val = os.environ.get("SPLOCK_CHAIN_ID")
    return val if val else None


def _operator_override_active() -> bool:
    return os.environ.get("OPERATOR_OVERRIDE_STATE") == "1"


def _overnight_mode() -> bool:
    return os.environ.get("OVERNIGHT_MODE") == "1"


def _invoke_render_status_tree(plan_dir: Path) -> int:
    """Regenerate `<slug>_orchestrator_status.md` — DAG tree + status glyphs.

    Per operator request 2026-05-24: combines the static DAG (from
    `<slug>_orchestrator.json`) with the live per-task status (from
    `_state.json`) into a single tree view. Sits alongside the existing
    `_orchestrator.md` (flat status table) and `_orchestrator_log.md`
    (audit log) renderers.

    Returns `_render_status_tree._render_one` exit code:
        0 = render success
        1 = missing orchestrator JSON (warning, stub MD)
        2 = corrupt JSON
        3 = output write failed

    Render failures do NOT lose state — caller treats non-zero as
    warning-only (state mutation already durable).
    """
    from bin._render_status_tree.main import _render_one

    return _render_one(plan_dir, output=None)


def _invoke_render_log(plan_dir: Path) -> int:
    """Regenerate `_orchestrator_log.md` from `_orchestrator_log.jsonl`.

    Per splock design v2.7 §1.E.2.iii / §4359: `bin/update_orchestrator`
    "regenerates `_orchestrator_log.md` via `bin/render_log`" after every
    successful state mutation. Without this wiring, the derived MD never
    gets regenerated, only the canonical JSONL grows.

    Calls `_render_one(plan_dir, ...)` directly rather than the slug-based
    CLI entry, so a test (or any caller working in a non-default repo
    root) can drive the render against an arbitrary directory.

    Returns the `_render_log._render_one` exit code:
        0 = render success
        1 = missing/empty JSONL (warning, empty MD)
        2 = corrupt JSONL detected
        3 = output write failed

    Render failures do NOT lose state — the canonical `_state.json` and
    `_orchestrator_log.jsonl` writes already succeeded by the time we
    invoke render_log.
    """
    from bin._render_log.main import _render_one

    return _render_one(
        plan_dir,
        output=None,
        since=None,
        llm_consumable=False,
    )


def _render_and_emit(
    plan_dir: Path,
    plan_slug: str,
    *,
    emitted_by: str,
) -> Optional[int]:
    """Run the wired `_orchestrator.md` render + emit the matching log row.

    Per orch_status_render T4 — invoked from both `_dispatch_base` and
    `_dispatch_from_develop_plan` AFTER `write_state` (so `_state.json`
    is durable) and BEFORE `emit_transition` (so the observability row
    precedes the transition row in JSONL ordering).

    Returns `None` on success, or a closed-enum exit code from
    `bin._render_plan.exit_codes` on render failure. The caller emits
    the actual transition row regardless of render outcome — the
    `_state.json` write is durable; we never lose the audit row for it.

    The caller MUST hold `state_flock(plan_dir)`; the renderer is
    invoked with `assume_flock_held=True` so it does not re-acquire.
    """
    # Imports are lazy: keep the §E module surface small + decouple the
    # cross-CLI dependency (so `from bin._update_orchestrator.main` does
    # not transitively force `_render_plan` import at module load).
    from bin._render_plan import exit_codes as render_exit_codes
    from bin._render_plan.atomic_write import AtomicWriteError
    from bin._render_plan.json_loader import (
        JsonMalformedError,
        PlanNotFoundError,
        SchemaRejectedError,
        UnsupportedSchemaVersion,
    )
    from bin._render_plan.md_renderer import TemplateError
    from bin._render_plan.render_invoker import render_state_under_flock

    try:
        render_state_under_flock(plan_dir, assume_flock_held=True)
    except PlanNotFoundError as exc:
        emit_state_md_render_failed(
            plan_dir=plan_dir,
            plan_slug=plan_slug,
            emitted_by=emitted_by,
            error=f"plan_not_found: {exc}",
            chain_id=_chain_id(),
            overnight=_overnight_mode(),
        )
        return render_exit_codes.EXIT_PLAN_NOT_FOUND
    except JsonMalformedError as exc:
        emit_state_md_render_failed(
            plan_dir=plan_dir,
            plan_slug=plan_slug,
            emitted_by=emitted_by,
            error=f"json_malformed: {exc}",
            chain_id=_chain_id(),
            overnight=_overnight_mode(),
        )
        return render_exit_codes.EXIT_JSON_MALFORMED
    except UnsupportedSchemaVersion as exc:
        emit_state_md_render_failed(
            plan_dir=plan_dir,
            plan_slug=plan_slug,
            emitted_by=emitted_by,
            error=f"unsupported_schema_version: {exc}",
            chain_id=_chain_id(),
            overnight=_overnight_mode(),
        )
        return render_exit_codes.EXIT_UNSUPPORTED_SCHEMA_VERSION
    except SchemaRejectedError as exc:
        emit_state_md_render_failed(
            plan_dir=plan_dir,
            plan_slug=plan_slug,
            emitted_by=emitted_by,
            error=f"schema_rejected: {exc}",
            chain_id=_chain_id(),
            overnight=_overnight_mode(),
        )
        return render_exit_codes.EXIT_SCHEMA_REJECTED
    except TemplateError as exc:
        emit_state_md_render_failed(
            plan_dir=plan_dir,
            plan_slug=plan_slug,
            emitted_by=emitted_by,
            error=f"template_error: {exc}",
            chain_id=_chain_id(),
            overnight=_overnight_mode(),
        )
        return render_exit_codes.EXIT_TEMPLATE_ERROR
    except AtomicWriteError as exc:
        emit_state_md_render_failed(
            plan_dir=plan_dir,
            plan_slug=plan_slug,
            emitted_by=emitted_by,
            error=f"atomic_write_failed: {exc}",
            chain_id=_chain_id(),
            overnight=_overnight_mode(),
        )
        return render_exit_codes.EXIT_ATOMIC_WRITE_FAILED
    except Exception as exc:
        # Defensive: any other unexpected exception still emits a failure
        # row and surfaces a non-zero exit (mapped to atomic-write-failed
        # as the most-generic write-side failure code).
        emit_state_md_render_failed(
            plan_dir=plan_dir,
            plan_slug=plan_slug,
            emitted_by=emitted_by,
            error=f"unexpected: {type(exc).__name__}: {exc}",
            chain_id=_chain_id(),
            overnight=_overnight_mode(),
        )
        return render_exit_codes.EXIT_ATOMIC_WRITE_FAILED

    # Success path — emit the observability row.
    emit_state_md_rendered(
        plan_dir=plan_dir,
        plan_slug=plan_slug,
        emitted_by=emitted_by,
        chain_id=_chain_id(),
        overnight=_overnight_mode(),
    )
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin/update_orchestrator",
        description=(
            "Update canonical task status in `<slug>/_state.json`. "
            "See docs/plans/splock/splock_implplan.md §E.impl."
        ),
    )
    # Mode flags (mutually exclusive: --from-develop-plan vs positional base form)
    parser.add_argument(
        "--from-develop-plan",
        action="store_true",
        dest="from_develop_plan",
        help="Consume develop-plan native 6-status and apply 6→7 mapping",
    )
    parser.add_argument(
        "--to-develop-plan",
        action="store_true",
        dest="to_develop_plan",
        help="(refused — reverse mapping is not supported)",
    )
    parser.add_argument(
        "--close",
        action="store_true",
        dest="close",
        help=(
            "Explicit operator close: mark <slug>'s orchestrator closed "
            "(writes `_state.json` lifecycle=closed + `_orchestrator_closed.lock`). "
            "Idempotent. Takes only <slug> (no task_id/status)."
        ),
    )
    parser.add_argument("slug", help="Plan slug (matches docs/plans/<slug>/)")
    # task_id + status are required for the base + --from-develop-plan
    # status-transition modes, but NOT for --close (which takes only <slug>).
    # nargs="?" makes them optional at parse time; dispatch enforces the
    # per-mode requirement.
    parser.add_argument(
        "task_id", nargs="?", default=None, help="Task ID (e.g., T1)"
    )
    parser.add_argument(
        "status",
        nargs="?",
        default=None,
        help=(
            "Status. Base mode: canonical 7-status enum value. "
            "--from-develop-plan mode: native 6-status "
            "(not_started/in_progress/awaiting_eval/revisions_requested:R<n>/completed/blocked)"
        ),
    )
    parser.add_argument("--pointer", default=None, help="Optional pointer string")
    parser.add_argument(
        "--reason",
        default=None,
        help="Optional free-form reason override (default auto-generated)",
    )
    return parser


def dispatch(args: argparse.Namespace, *, repo_root: Optional[Path] = None) -> int:
    if args.to_develop_plan:
        emit_refusal(
            {
                "error": "reverse_mapping_unsupported",
                "reason": "--to-develop-plan is not a supported invocation surface.",
            }
        )
        return EXIT_USAGE

    plan_dir = _plan_dir(args.slug, repo_root)
    plan_dir.mkdir(parents=True, exist_ok=True)

    if getattr(args, "close", False):
        # --close is mutually exclusive with the status-transition modes and
        # takes ONLY <slug>; reject stray positional args / mode combos.
        if args.from_develop_plan:
            emit_refusal(
                {
                    "error": "usage",
                    "reason": "--close cannot be combined with --from-develop-plan.",
                }
            )
            return EXIT_USAGE
        if args.task_id is not None or args.status is not None:
            emit_refusal(
                {
                    "error": "usage",
                    "reason": "--close takes only <slug> (no task_id/status).",
                }
            )
            return EXIT_USAGE
        return _dispatch_close(args, plan_dir)

    # Base + --from-develop-plan modes both require task_id + status.
    if args.task_id is None or args.status is None:
        emit_refusal(
            {
                "error": "usage",
                "reason": (
                    "task_id and status are required (omit them only with --close)."
                ),
            }
        )
        return EXIT_USAGE

    if args.from_develop_plan:
        return _dispatch_from_develop_plan(args, plan_dir)
    return _dispatch_base(args, plan_dir)


def _dispatch_close(args: argparse.Namespace, plan_dir: Path) -> int:
    """`--close` mode — explicit operator close of the orchestrator (T4 / SC4).

    Marks the plan closed via the shared `close_orchestrator` helper (writes
    `_state.json` lifecycle=closed + `_orchestrator_closed.lock` marker;
    appends an `orchestrator_closed` audit row). Idempotent: a second
    `--close` is a no-op and still exits 0. The orchestrator JSON is never
    mutated. Holds `state_flock` for the RMW (the helper acquires it).
    """
    from .closed_state import close_orchestrator

    result = close_orchestrator(
        plan_dir,
        slug=args.slug,
        trigger="operator_close",
        reason=args.reason or "explicit operator close (bin/update_orchestrator --close)",
    )
    if result.already_closed:
        print(
            f"already-closed: {args.slug} (idempotent no-op)",
            file=sys.stderr,
        )
    else:
        print(
            f"Closed {args.slug} ({result.closed_at}): lifecycle=closed",
            file=sys.stderr,
        )
        # fleet auto-integration (docs/FLEET.md): archive the slug on the
        # hub. Silent no-op unless the project ran `bin/fleet init`.
        fleet_auto.slug_closed(
            args.slug,
            note=args.reason or "orchestrator closed (bin/update_orchestrator --close)",
        )
    return EXIT_OK


def _dispatch_base(args: argparse.Namespace, plan_dir: Path) -> int:
    """Base CLI form — accepts a canonical 7-status enum value directly."""
    target_status = args.status

    with state_flock(plan_dir):
        state = read_state(plan_dir)
        current_status = get_task_status(state, args.task_id) or "ready"

        verdict = validate_transition(
            from_status=current_status,
            to_status=target_status,
            override_active=_operator_override_active(),
        )
        if verdict.kind == VERDICT_REFUSE_UNKNOWN_STATUS:
            emit_refusal(
                refusal_schema_rejected(
                    reason=(
                        f"transition {current_status!r} → {target_status!r} "
                        "uses an unknown 7-status enum value"
                    ),
                    task_id=args.task_id,
                )
            )
            return EXIT_SCHEMA_REJECTED
        if verdict.kind == VERDICT_REFUSE_DONE_WIP_NO_OVERRIDE:
            return _handle_done_wip_refusal(
                plan_dir=plan_dir,
                slug=args.slug,
                task_id=args.task_id,
                emitted_by=EMIT_BASE,
            )

        # Allowed transition — write
        set_task_status(state, args.task_id, target_status)
        write_state(plan_dir, state)

        # orch_status_render T4 — wire the renderer call inside the
        # flock, after the state write is durable, before the transition
        # row is emitted. Render failures still allow the transition row
        # to be emitted (audit-row invariant) but propagate a non-zero
        # exit code drawn from bin/_render_plan/exit_codes.
        render_failure_code = _render_and_emit(
            plan_dir=plan_dir,
            plan_slug=args.slug,
            emitted_by=EMIT_BASE,
        )

        reason = args.reason or f"status transition {current_status} → {target_status}"
        override_active = _operator_override_active()
        override_payload = (
            {"operator_override_state": True} if override_active else None
        )
        # Emit a row for the overnight-override "loud-warning" path (per
        # E.impl.10 #4): when an operator-override applies under overnight
        # mode, stamp an extra payload field so morning-review can surface.
        extra: Optional[dict] = None
        if override_active and _overnight_mode():
            extra = {"event_type": "overnight_rollback_override_used"}
        emit_transition(
            plan_dir=plan_dir,
            plan_slug=args.slug,
            task_id=args.task_id,
            transition_from=current_status,
            transition_to=target_status,
            reason=reason,
            emitted_by=EMIT_BASE,
            chain_id=_chain_id(),
            override_in_effect=override_payload,
            overnight=_overnight_mode(),
            guardrail=False,
            pointer=args.pointer,
            extra=extra,
        )

        # Regenerate `_orchestrator_log.md` from the JSONL (canonical →
        # derived per design v2.7 §1.E.2.iii). Held INSIDE state_flock to
        # match the `_render_and_emit` invariant — any wired render runs
        # while the dispatch still holds the lock. Render failures are
        # non-fatal: the canonical state-mutation has already succeeded;
        # a missing/corrupt MD is operator-recoverable via
        # `bin/render_log <slug>`.
        log_render_code = _invoke_render_log(plan_dir)
        if log_render_code not in (0, 1):
            print(
                f"warning: render_log exited {log_render_code} for slug={args.slug!r} "
                f"(state mutation still durable; re-run `bin/render_log {args.slug}` to retry)",
                file=sys.stderr,
            )

        status_tree_code = _invoke_render_status_tree(plan_dir)
        if status_tree_code not in (0, 1):
            print(
                f"warning: render_status_tree exited {status_tree_code} for slug={args.slug!r} "
                f"(state mutation still durable; re-run `bin/render_status_tree {args.slug}` to retry)",
                file=sys.stderr,
            )

    # fleet auto-integration (docs/FLEET.md): the task transition is durable
    # at this point (render failures above never roll it back). Silent no-op
    # unless the adopter project ran `bin/fleet init`; never raises.
    fleet_auto.code_task_updated(args.slug, args.task_id, target_status)

    if render_failure_code is not None:
        return render_failure_code
    return EXIT_OK


def _dispatch_from_develop_plan(
    args: argparse.Namespace, plan_dir: Path
) -> int:
    """`--from-develop-plan` mode — consume native 6-status, apply mapping."""
    native = args.status

    # 1. Parse + map (NativeStatusParseError → exit 4)
    try:
        mapping = map_develop_plan_to_canonical(native)
    except NativeStatusParseError as exc:
        emit_refusal(
            refusal_schema_rejected(reason=str(exc), task_id=args.task_id, native=native)
        )
        return EXIT_SCHEMA_REJECTED

    with state_flock(plan_dir):
        state = read_state(plan_dir)
        current_status = get_task_status(state, args.task_id) or "ready"

        # 2. Authority check (deferred/abandoned/cancelled → exit 18)
        try:
            ensure_task_in_authority(current_status, args.task_id)
        except TaskOutsideAuthorityError:
            emit_refusal(
                refusal_task_outside_develop_plan_authority(
                    task_id=args.task_id, status=current_status
                )
            )
            return EXIT_TASK_OUTSIDE_DEVELOP_PLAN_AUTHORITY

        target_status = mapping.canonical

        # 3. Canonical transition validation (done → wip operator gate)
        verdict = validate_transition(
            from_status=current_status,
            to_status=target_status,
            override_active=_operator_override_active(),
        )
        if verdict.kind == VERDICT_REFUSE_UNKNOWN_STATUS:
            emit_refusal(
                refusal_schema_rejected(
                    reason=(
                        f"transition {current_status!r} → {target_status!r} uses unknown 7-status enum"
                    ),
                    task_id=args.task_id,
                )
            )
            return EXIT_SCHEMA_REJECTED
        if verdict.kind == VERDICT_REFUSE_DONE_WIP_NO_OVERRIDE:
            return _handle_done_wip_refusal(
                plan_dir=plan_dir,
                slug=args.slug,
                task_id=args.task_id,
                emitted_by=EMIT_FROM_DEVELOP_PLAN,
            )

        # 4. Apply side-effect (iteration_history append for R<n>)
        task_entry = get_task_entry(state, args.task_id)
        try:
            check_dual_retry_cap_mutex(task_entry)
        except DualRetryCapMutexViolation:
            emit_refusal(refusal_dual_retry_cap_mutex(task_id=args.task_id))
            return EXIT_DUAL_RETRY_CAP_MUTEX_VIOLATED

        if mapping.side_effect == SIDE_EFFECT_APPEND_ITERATION:
            try:
                append_iteration_row(task_entry, round_n=mapping.round_n)
            except IterationOverflowError:
                emit_refusal(refusal_iteration_overflow(task_id=args.task_id))
                # Also emit a JSONL row for the overflow event so morning-review surfaces it.
                emit_transition(
                    plan_dir=plan_dir,
                    plan_slug=args.slug,
                    task_id=args.task_id,
                    transition_from=current_status,
                    transition_to=current_status,
                    reason="iteration_overflow_sentinel: storage cap exhausted",
                    emitted_by=EMIT_FROM_DEVELOP_PLAN,
                    chain_id=_chain_id(),
                    overnight=_overnight_mode(),
                    guardrail=False,
                    extra={"event_type": "iteration_overflow_sentinel"},
                )
                return EXIT_ITERATION_OVERFLOW_REFUSED
            except DualRetryCapMutexViolation:
                emit_refusal(refusal_dual_retry_cap_mutex(task_id=args.task_id))
                return EXIT_DUAL_RETRY_CAP_MUTEX_VIOLATED
            except TelemetryUnsupportedVersionError as exc:
                emit_refusal(
                    refusal_unsupported_schema_version(
                        kind="develop_plan_telemetry",
                        seen=task_entry.get("develop_plan_telemetry", {}).get(
                            "schema_version", 0
                        ),
                        supported=list(SUPPORTED_VERSIONS_TELEMETRY),
                    )
                )
                return EXIT_UNSUPPORTED_SCHEMA_VERSION
            except TelemetrySchemaError as exc:
                emit_refusal(
                    refusal_schema_rejected(
                        reason=str(exc),
                        task_id=args.task_id,
                        kind="develop_plan_telemetry",
                    )
                )
                return EXIT_SCHEMA_REJECTED

        # 5. Write canonical status
        set_task_status(state, args.task_id, target_status)
        write_state(plan_dir, state)

        # orch_status_render T4 — wire the renderer call inside the
        # flock, after `write_state` (and after the sidecar
        # iteration_history append from step 4 if it ran), before the
        # transition row is emitted. Same contract as `_dispatch_base`:
        # render failure does NOT lose the transition audit row; it
        # propagates a non-zero exit code drawn from
        # bin/_render_plan/exit_codes.
        render_failure_code = _render_and_emit(
            plan_dir=plan_dir,
            plan_slug=args.slug,
            emitted_by=EMIT_FROM_DEVELOP_PLAN,
        )

        # 6. Emit row
        override_active = _operator_override_active()
        override_payload = (
            {"operator_override_state": True} if override_active else None
        )
        extra = {"event_type": "develop_plan_status_mapped", "native_status": native}
        if override_active and _overnight_mode() and current_status == "done" and target_status == "wip":
            extra["event_type"] = "overnight_rollback_override_used"
        reason = (
            args.reason
            or f"develop-plan {native!r} → canonical {target_status} (was {current_status})"
        )
        emit_transition(
            plan_dir=plan_dir,
            plan_slug=args.slug,
            task_id=args.task_id,
            transition_from=current_status,
            transition_to=target_status,
            reason=reason,
            emitted_by=EMIT_FROM_DEVELOP_PLAN,
            chain_id=_chain_id(),
            override_in_effect=override_payload,
            overnight=_overnight_mode(),
            guardrail=False,
            pointer=args.pointer,
            extra=extra,
        )

        # Regenerate `_orchestrator_log.md` (see `_dispatch_base` for rationale).
        log_render_code = _invoke_render_log(plan_dir)
        if log_render_code not in (0, 1):
            print(
                f"warning: render_log exited {log_render_code} for slug={args.slug!r} "
                f"(state mutation still durable; re-run `bin/render_log {args.slug}` to retry)",
                file=sys.stderr,
            )

        status_tree_code = _invoke_render_status_tree(plan_dir)
        if status_tree_code not in (0, 1):
            print(
                f"warning: render_status_tree exited {status_tree_code} for slug={args.slug!r} "
                f"(state mutation still durable; re-run `bin/render_status_tree {args.slug}` to retry)",
                file=sys.stderr,
            )

    # fleet auto-integration (docs/FLEET.md): the task transition is durable
    # at this point (render failures above never roll it back). Silent no-op
    # unless the adopter project ran `bin/fleet init`; never raises.
    fleet_auto.code_task_updated(args.slug, args.task_id, target_status)

    if render_failure_code is not None:
        return render_failure_code
    return EXIT_OK


def _handle_done_wip_refusal(
    *,
    plan_dir: Path,
    slug: str,
    task_id: str,
    emitted_by: str,
) -> int:
    """Emit refusal stderr + forensic JSONL row; return appropriate exit code.

    Per implplan §E.impl.5 step 8:
    - In chain context (`SPLOCK_CHAIN_ID` set): exit 10 (`phase_boundary_halt`)
    - Interactive context: exit 19 (`done_wip_rollback_refused`)
    """
    emit_refusal(refusal_done_wip_rollback(task_id=task_id))
    # Forensic row: keep `transition.to` within the 7-status enum (the
    # schema rejects anything else). The refusal kind is conveyed via the
    # `event_type` payload field + the `reason` string.
    emit_transition(
        plan_dir=plan_dir,
        plan_slug=slug,
        task_id=task_id,
        transition_from="done",
        transition_to="done",  # state did NOT change; transition is the attempted-and-refused boundary
        reason=(
            "develop_plan_rollback_refused: develop-plan attempted done → wip "
            "rollback without OPERATOR_OVERRIDE_STATE=1"
        ),
        emitted_by=emitted_by,
        chain_id=_chain_id(),
        override_in_effect={"operator_override_state": False},
        overnight=_overnight_mode(),
        guardrail=False,
        extra={"event_type": "develop_plan_rollback_refused"},
    )
    if _is_chain_context():
        return EXIT_PHASE_BOUNDARY_HALT
    return EXIT_DONE_WIP_ROLLBACK_REFUSED


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return dispatch(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
