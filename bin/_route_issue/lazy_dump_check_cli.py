"""CLI entry for `bin/lazy-dump-check` (implplan §L.impl.7).

Three invocation modes:

  --pre-commit     hook-driven; refuses with exit 26 if hard cap breached;
                   stderr WARN if soft cap breached; exit 0 if clean
  --status         print current counters; never refuses
  --reset-session  operator-only; zero per-session counter (logged via §C)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import lazy_dump_cap, log_emit
from .exit_codes import EXIT_OK, EXIT_OUTSTANDING_CAP_EXCEEDED, EXIT_USAGE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin/lazy-dump-check",
        description=(
            "Lazy-dump-cap checker (implplan §L.impl.7). Pre-commit gate + "
            "operator status surface."
        ),
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--pre-commit", action="store_true", dest="pre_commit",
                     help="hook-driven; refuses on hard cap")
    grp.add_argument("--status", action="store_true",
                     help="print counters; never refuses")
    grp.add_argument("--reset-session", action="store_true", dest="reset_session",
                     help="operator-only; zero the per-session counter")
    return parser


def dispatch(args: argparse.Namespace) -> int:
    threshold = lazy_dump_cap.cap_threshold()
    session = lazy_dump_cap.session_count()
    machine = lazy_dump_cap.machine_count()
    soft_cap = lazy_dump_cap.MACHINE_SOFT_CAP_PER_HOUR

    if args.status:
        if args.json_output:
            print(json.dumps({
                "session_count": session,
                "session_cap": threshold,
                "machine_count_last_hour": machine,
                "machine_soft_cap_per_hour": soft_cap,
            }))
        else:
            print(f"session: {session}/{threshold}")
            print(f"machine (rolling hour): {machine}/{soft_cap} (soft)")
        return EXIT_OK

    if args.reset_session:
        lazy_dump_cap.reset_session()
        # Emit forensic log row via §C
        plan_dir = log_emit.resolve_plan_dir(None, None)
        log_emit.emit_row(
            plan_dir=plan_dir,
            plan_slug="splock",
            transition_from="deferred",
            transition_to="ready",
            reason="lazy_dump_cap_reset: per-session counter zeroed (operator)",
            emitted_by=log_emit.EMIT_LAZY_DUMP,
            extra={"event_type": "lazy_dump_cap_reset"},
        )
        if args.json_output:
            print(json.dumps({"result": "reset", "session_count": 0}))
        else:
            print("session counter reset to 0")
        return EXIT_OK

    if args.pre_commit:
        # Hard-cap refusal
        if session > threshold:
            if args.json_output:
                print(json.dumps({
                    "error": "outstanding_cap_exceeded",
                    "session_count": session,
                    "cap": threshold,
                }))
            else:
                print(
                    f"REFUSED [outstanding_cap_exceeded]: session={session} "
                    f"exceeds cap={threshold}",
                    file=sys.stderr,
                )
            # Forensic row
            plan_dir = log_emit.resolve_plan_dir(None, None)
            log_emit.emit_row(
                plan_dir=plan_dir,
                plan_slug="splock",
                transition_from="deferred",
                transition_to="blocked",
                reason=(
                    f"lazy_dump_cap_exceeded: session={session} cap={threshold}"
                ),
                emitted_by=log_emit.EMIT_LAZY_DUMP,
                extra={"event_type": "lazy_dump_cap_exceeded"},
            )
            return EXIT_OUTSTANDING_CAP_EXCEEDED
        # Soft-cap warning (no refuse)
        if machine > soft_cap:
            print(
                f"WARN: lazy-dump per-machine soft cap exceeded "
                f"({machine}/{soft_cap} in last hour)",
                file=sys.stderr,
            )
        if args.json_output:
            print(json.dumps({
                "result": "clean",
                "session_count": session,
                "cap": threshold,
            }))
        return EXIT_OK

    return EXIT_USAGE


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
