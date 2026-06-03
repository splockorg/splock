"""CLI entry point for `bin/intent` (implplan §P.impl.3).

Dispatches to one of seven subcommand modules. The POSIX shell wrapper
at `bin/intent` calls `python -m bin._intent.main "$@"`.
"""

from __future__ import annotations

import sys
from typing import List, Optional

from . import (
    check,
    cli,
    complete,
    doctor,
    list_sessions,
    pivot,
    register,
    update,
)
from .exit_codes import EXIT_OK, EXIT_USAGE


def _resolve_ttl_minutes() -> int:
    """Resolve `intent.ttl_minutes` via the framework-internal resolver.

    SC-C #3: replaces the pre-extraction ``console.settings_registry`` +
    ``src.DAL.DAL.from_pool()`` plumbing with the zero-DB resolver in
    :mod:`bin._intent.settings`. Defensive fallback to 240 mirrors the
    legacy contract (any failure → documented default).
    """
    try:
        from . import settings as intent_settings
        return int(intent_settings.resolve("intent.ttl_minutes", 240))
    except Exception:  # noqa: BLE001
        return 240


def main(argv: Optional[List[str]] = None) -> int:
    parser = cli.build_parser()
    args = parser.parse_args(argv)

    sub = args.subcommand
    if sub == "check":
        return check.run(
            area=args.area,
            paths=cli.parse_paths(args.paths),
            host=args.host,
            json_output=args.json_output,
        )
    if sub == "register":
        rc = register.run(
            area=args.area,
            paths=cli.parse_paths(args.paths),
            kind=args.kind,
            closure=args.closure,
            design_pattern=args.design_pattern,
            plan=args.plan,
            chain_id=args.chain_id,
            emitted_by=args.emitted_by,
            ttl_minutes=_resolve_ttl_minutes(),
            dry_run=args.dry_run,
            json_output=args.json_output,
            # T1 (intent_session_auto_register): thread the new --claude-session-id
            # flag through to the register call site. None when omitted.
            claude_session_id=getattr(args, "claude_session_id", None),
            upsert=getattr(args, "upsert", False),
        )
        # T5 (intent_session_auto_register): post-success lazy-doctor
        # trigger. Research Decision 3 trigger #2. Only fires on
        # successful register so collision_detected / closure-trigger
        # refusals don't burn a doctor window. Wrapped to ensure the
        # trigger never raises into the CLI exit path.
        if rc == EXIT_OK and not args.dry_run:
            try:
                from . import doctor_trigger
                doctor_trigger.trigger_background()
            except Exception:  # noqa: BLE001
                pass
        return rc
    if sub == "update":
        return update.run(
            session_id=args.session_id,
            paths=cli.parse_paths(args.paths) if args.paths else None,
            status=args.status,
            note=args.note,
            dry_run=args.dry_run,
            json_output=args.json_output,
        )
    if sub == "complete":
        return complete.run(
            session_id=args.session_id,
            reason=args.reason,
            dry_run=args.dry_run,
            json_output=args.json_output,
        )
    if sub == "list":
        # active is the default when none of {active, closed, all_sessions} set.
        active = args.active or not (args.closed or args.all_sessions)
        return list_sessions.run(
            area=args.area,
            kind=args.kind,
            host=args.host,
            active=active,
            closed=args.closed,
            all_sessions=args.all_sessions,
            json_output=args.json_output,
            # T1 (intent_session_auto_register): forward the new
            # --claude-session filter through to list_sessions.
            claude_session=getattr(args, "claude_session", None),
        )
    if sub == "pivot":
        return pivot.run(
            session_id=args.session_id,
            area=args.new_area,
            paths=cli.parse_paths(args.paths) if args.paths else None,
            dry_run=args.dry_run,
            json_output=args.json_output,
        )
    if sub == "doctor":
        # T5 (intent_session_auto_register): research Decision 3 trigger
        # #3 — manual `bin/intent doctor` ALWAYS runs and resets the
        # timestamp so subsequent lazy triggers honor the fresh window.
        # The reset happens regardless of dry_run because operator
        # intent is "I want a doctor now"; even a dry-run satisfies
        # that intent for the rate-limit purpose.
        if not args.dry_run:
            try:
                from . import doctor_trigger
                doctor_trigger.reset_timestamp()
            except Exception:  # noqa: BLE001
                pass
        doctor_rc = doctor.run(
            dry_run=args.dry_run,
            json_output=args.json_output,
            reconcile_sync_pending=args.reconcile_sync_pending,
        )
        # T5 — emit a hook-log row tagged `intent.doctor` so
        # `bin/morning-review`'s hook-log triage helper (see
        # `bin._morning_review.hook_log_triage`) can categorize
        # doctor outcomes distinctly in the daily review. Research
        # Decision 3 mandate.
        try:
            from bin._hooks.log_emit import emit as _hook_emit
            action = "ok" if doctor_rc == EXIT_OK else "error"
            _hook_emit(
                mode="hook",
                name="intent.doctor",
                action=action,
                message=f"manual doctor run rc={doctor_rc}",
            )
        except Exception:  # noqa: BLE001
            pass
        return doctor_rc

    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
