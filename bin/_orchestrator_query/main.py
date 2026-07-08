"""CLI entry point for `bin/orchestrator-next-ready` (code_next_ready_pick T1).

Usage::

    bin/orchestrator-next-ready <slug>             # prints first-ready task-id, exit 0
    bin/orchestrator-next-ready <slug> --json      # emits ReadySetReport JSON on stdout
    bin/orchestrator-next-ready <slug> --verbose   # per-task breakdown on stderr + first-ready on stdout

Exit codes (closed-enum, `bin/_orchestrator_query/exit_codes.py`):

    0  OK — first-ready task printed
    2  USAGE — argparse / bad CLI surface
    10 SLUG_NOT_FOUND — docs/plans/<slug>/ missing
    11 ORCHESTRATOR_JSON_MISSING — plan dir exists, JSON missing
    12 ORCHESTRATOR_JSON_MALFORMED — unparseable orchestrator
    13 STATE_SHAPE_INVALID — _state.json neither dict nor array (post-T0 narrowing)
    20 NO_READY_TASK_ALL_BLOCKED — verdict=all_blocked
    21 NO_READY_TASK_ALL_WIP — verdict=all_wip
    22 NO_READY_TASK_MIXED — verdict=mixed_no_ready
    23 PLAN_COMPLETE_ALL_DONE — verdict=all_done

The picker is a read-only surface — no `_state.json` mutations, no JSONL
emissions. State mutations stay with `bin/update_orchestrator` per the
substrate's single-writer discipline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from bin._env_paths import project_root as _env_project_root

from . import exit_codes
from .orchestrator_loader import (
    OrchestratorJsonMalformedError,
    OrchestratorJsonMissingError,
    SlugNotFoundError,
    load_orchestrator,
)
from .query import ReadySetReport, compute_ready_set
from .refusal import (
    emit_refusal,
    refusal_no_ready_all_blocked,
    refusal_no_ready_all_wip,
    refusal_no_ready_mixed,
    refusal_orchestrator_json_malformed,
    refusal_orchestrator_json_missing,
    refusal_plan_complete_all_done,
    refusal_slug_not_found,
    refusal_state_shape_invalid,
)
from .state_loader import StateShapeInvalidError, load_state


def _repo_root() -> Path:
    """Resolve the adopter project root, honouring ``$CLAUDE_PROJECT_DIR``.

    Delegates to ``bin._env_paths.project_root()`` so the picker resolves the
    ADOPTER's ``docs/plans/`` (not the plugin install tree) when splock runs
    as an installed plugin against a foreign project. Same fix class as OI-1,
    which rewired the eight planner/render/chain entry points but missed this
    one (fork finding F2).
    """
    return _env_project_root()


def _plan_dir(slug: str, repo_root: Optional[Path] = None) -> Path:
    root = repo_root or _repo_root()
    return root / "docs" / "plans" / slug


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin/orchestrator-next-ready",
        description=(
            "Print the next-ready task id for <slug>'s orchestrator. "
            "See docs/plans/code_next_ready_pick/code_next_ready_pick_plan.md."
        ),
    )
    parser.add_argument(
        "slug",
        help="Plan slug (matches docs/plans/<slug>/).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="emit_json",
        help="Emit the full ReadySetReport as JSON on stdout instead of just the task-id.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        dest="verbose",
        help="Write a per-task breakdown to stderr; still prints the first-ready id to stdout on exit 0.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 2 on bad CLI; preserve that semantic via our
        # closed enum (which uses 2 as USAGE).
        if exc.code == 0:
            return exit_codes.EXIT_OK
        return exit_codes.EXIT_USAGE

    try:
        return dispatch(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


def dispatch(args: argparse.Namespace, *, repo_root: Optional[Path] = None) -> int:
    """Core CLI flow: load → compute → emit → exit code."""
    plan_dir = _plan_dir(args.slug, repo_root)

    # Step 1: load the orchestrator (resolves SLUG_NOT_FOUND /
    # ORCHESTRATOR_JSON_MISSING / ORCHESTRATOR_JSON_MALFORMED).
    try:
        orchestrator = load_orchestrator(plan_dir, args.slug)
    except SlugNotFoundError:
        emit_refusal(
            refusal_slug_not_found(slug=args.slug, plan_dir=str(plan_dir))
        )
        return exit_codes.EXIT_SLUG_NOT_FOUND
    except OrchestratorJsonMissingError:
        path = plan_dir / f"{args.slug}_orchestrator.json"
        emit_refusal(
            refusal_orchestrator_json_missing(slug=args.slug, path=str(path))
        )
        return exit_codes.EXIT_ORCHESTRATOR_JSON_MISSING
    except OrchestratorJsonMalformedError as exc:
        path = plan_dir / f"{args.slug}_orchestrator.json"
        emit_refusal(
            refusal_orchestrator_json_malformed(
                slug=args.slug, path=str(path), reason=str(exc)
            )
        )
        return exit_codes.EXIT_ORCHESTRATOR_JSON_MALFORMED

    # Step 2: load + normalize _state.json (STATE_SHAPE_INVALID on bad).
    try:
        state = load_state(plan_dir)
    except StateShapeInvalidError as exc:
        path = plan_dir / "_state.json"
        emit_refusal(
            refusal_state_shape_invalid(
                slug=args.slug, path=str(path), reason=str(exc)
            )
        )
        return exit_codes.EXIT_STATE_SHAPE_INVALID

    # Step 3: compute the ready-set.
    report = compute_ready_set(orchestrator, state)

    # Step 4: optional verbose breakdown to stderr.
    if args.verbose:
        _emit_verbose(report)

    # Step 5: emit + exit per verdict.
    return _emit_and_exit(args, report, plan_dir=plan_dir)


def _emit_and_exit(
    args: argparse.Namespace, report: ReadySetReport, *, plan_dir: Path
) -> int:
    """Emit the appropriate stdout/stderr payload and return the exit code."""
    if report.verdict == "has_ready":
        if args.emit_json:
            sys.stdout.write(json.dumps(report.to_jsonable(), sort_keys=True) + "\n")
        else:
            sys.stdout.write(f"{report.first_ready}\n")
        return exit_codes.EXIT_OK

    # No-ready verdicts: emit a structured refusal to stderr regardless
    # of --json (since stdout is reserved for the picker's positive
    # answer). When --json is set, the report is also written to stdout
    # so machine consumers can inspect the breakdown without losing the
    # refusal-class signal.
    if args.emit_json:
        sys.stdout.write(json.dumps(report.to_jsonable(), sort_keys=True) + "\n")

    if report.verdict == "all_done":
        terminal_count = (
            len(report.done_ids)
            + len(report.cancelled_ids)
            + len(report.deferred_ids)
        )
        # Auto-close trigger (plan_surgical_amend T4 / SC4): the FIRST time a
        # plan reaches all_done, stamp the closed-state lifecycle (`_state.json`
        # `lifecycle: closed` + `_orchestrator_closed.lock` marker). This is the
        # SAME exit-23 path the live `bin/orchestrator-next-ready` picker runs on
        # every invocation, so the call is wrapped best-effort: it is idempotent
        # (no-op once the marker exists) and MUST NEVER raise into the picker — a
        # close-side failure degrades to a warning and the picker still returns
        # exit 23. The close write touches ONLY `_state.json` + the marker; the
        # orchestrator JSON is never mutated.
        _maybe_auto_close(plan_dir, slug=args.slug)
        emit_refusal(
            refusal_plan_complete_all_done(
                slug=args.slug, terminal_count=terminal_count
            )
        )
        return exit_codes.EXIT_PLAN_COMPLETE_ALL_DONE

    if report.verdict == "all_blocked":
        emit_refusal(
            refusal_no_ready_all_blocked(
                slug=args.slug, blocked_ids=list(report.blocked_ids)
            )
        )
        return exit_codes.EXIT_NO_READY_TASK_ALL_BLOCKED

    if report.verdict == "all_wip":
        emit_refusal(
            refusal_no_ready_all_wip(
                slug=args.slug, wip_ids=list(report.wip_ids)
            )
        )
        return exit_codes.EXIT_NO_READY_TASK_ALL_WIP

    # mixed_no_ready
    summary = {
        "blocked": list(report.blocked_ids),
        "wip": list(report.wip_ids),
        "done": list(report.done_ids),
        "cancelled": list(report.cancelled_ids),
        "deferred": list(report.deferred_ids),
        "unknown": list(report.unknown_ids),
    }
    emit_refusal(
        refusal_no_ready_mixed(slug=args.slug, summary=summary)
    )
    return exit_codes.EXIT_NO_READY_TASK_MIXED


def _maybe_auto_close(plan_dir: Path, *, slug: str) -> None:
    """Best-effort closed-state stamp on the all_done verdict (T4 / SC4).

    Imported lazily (keeps the picker's read-only module surface small +
    decouples the cross-package dependency on `bin._update_orchestrator`).
    The close helper is itself idempotent (no-op once the marker exists);
    this wrapper additionally swallows ANY exception so the picker — which
    runs this exact path on every `bin/orchestrator-next-ready` invocation —
    never fails or changes its exit code because of a close-side error. A
    swallowed failure emits a one-line warning to stderr (state mutation, if
    it partially happened, is durable; re-running the picker re-attempts the
    idempotent close).
    """
    try:
        from bin._update_orchestrator.closed_state import close_orchestrator

        close_orchestrator(
            plan_dir,
            slug=slug,
            trigger="auto_all_done",
            reason="auto-close on first all_done verdict (picker)",
        )
    except Exception as exc:  # never propagate into the picker
        sys.stderr.write(
            f"warning: auto-close failed for slug={slug!r} "
            f"({type(exc).__name__}: {exc}); picker result unaffected\n"
        )


def _emit_verbose(report: ReadySetReport) -> None:
    """Write a per-task breakdown to stderr in a stable, line-oriented form."""
    sys.stderr.write(f"verdict: {report.verdict}\n")
    sys.stderr.write(f"ready_task_ids: {','.join(report.ready_task_ids) or '<none>'}\n")
    sys.stderr.write(f"first_ready: {report.first_ready or '<none>'}\n")
    sys.stderr.write("breakdown:\n")
    for entry in report.breakdown:
        unsat = ",".join(entry.unsatisfied_deps) or "-"
        sys.stderr.write(
            f"  {entry.id}: status={entry.effective_status} "
            f"ready={entry.is_ready} unsat_deps={unsat}\n"
        )


if __name__ == "__main__":
    sys.exit(main())
