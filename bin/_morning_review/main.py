"""CLI entry point for `bin/morning-review` (implplan §H.impl.3).

Invoked via the POSIX shell wrapper at `bin/morning-review` which
delegates to `python -m bin._morning_review.main`.

Dispatches to:
  - bootstrap.run                  for --internal-bootstrap-day
  - mark_deferred.run              for --internal-mark-deferred
  - triage_dispatch.{reactivate, route_outstanding, route_marker,
                     abandon, acknowledge}
  - archive.gc + index_regen       for `gc`
  - cli + queue_file               for `list` / `show`
  - v1.4 stub for mark-for-eval / label-score / retire-case

Exit codes per §H.impl.3 closed enum (see `exit_codes.py`).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import List, Optional

from . import (
    archive,
    bootstrap,
    cli,
    entry_format,
    eval_extensions,
    index_regen,
    log_emit,
    mark_deferred,
    queue_file,
    triage_dispatch,
)
from .exit_codes import (
    EXIT_ABANDON_ARGS_MISSING,
    EXIT_ARCHIVE_MOVE_FAILED,
    EXIT_OK,
    EXIT_QUEUE_ENTRY_NOT_FOUND,
    EXIT_USAGE,
)


def _repo_root() -> pathlib.Path:
    """bin/_morning_review/main.py → REPO_ROOT (two levels up)."""
    return pathlib.Path(__file__).resolve().parents[2]


# --- abandon arg validation (per §H.impl.8) -------------------------------

def _check_abandon_args(args: argparse.Namespace) -> Optional[str]:
    """Return None if args valid, else the operator-readable refusal text.

    Per §H.impl.8: requires `--confirm` AND non-empty `--reason`. The
    refusal goes to stderr at exit code 31.
    """
    if not args.confirm:
        return _abandon_refusal_text(args, missing="--confirm")
    if not (args.reason and args.reason.strip()):
        return _abandon_refusal_text(args, missing="--reason")
    return None


def _abandon_refusal_text(args: argparse.Namespace, *, missing: str) -> str:
    """Render the dry-run summary refusal per §H.impl.8 #1 example."""
    return (
        f"abandon requires {missing} to proceed (plan §H.3a).\n"
        f"Dry-run summary:\n"
        f"  task_id: {args.task_id}\n"
        f"  slug: {args.slug}\n"
        f"  original deferral reason: (see morning-review entry)\n"
        f"  lessons.md entry to create: <draft prompt; operator authors via bin/lessons add>\n"
        f'Re-invoke with --confirm and --reason "<text>" to abandon.\n'
    )


# --- list / show ----------------------------------------------------------

def _cmd_list(args: argparse.Namespace, repo_root: pathlib.Path) -> int:
    """List entries across open daily files."""
    show_all = args.all_entries
    rows: list[dict] = []
    for daily in queue_file.iter_open_daily_files(repo_root, args.slug):
        text = queue_file.read_daily(daily)
        for entry in entry_format.parse(text, warn_hook=lambda _m: None):
            if not show_all and entry.triage_mirror != "[pending]":
                continue
            rows.append(
                {
                    "task_id": entry.task_id,
                    "slug": entry.slug,
                    "chain_id": entry.chain_id,
                    "phase": entry.phase,
                    "deferral_reason": entry.deferral_reason_token,
                    "triage_mirror": entry.triage_mirror,
                    "daily_file": str(daily.relative_to(repo_root)),
                }
            )
    if not rows and args.slug is not None:
        # Empty result for an explicit slug is informational, not error.
        if args.json_output:
            print(json.dumps([]))
        else:
            print(f"(no entries for slug={args.slug})")
        return EXIT_OK
    if args.json_output:
        print(json.dumps(rows, indent=2))
    else:
        if not rows:
            print("(no open entries)")
        else:
            print("task_id\tslug\tchain_id\tphase\tdeferral_reason\ttriage")
            for r in rows:
                print(
                    f"{r['task_id']}\t{r['slug']}\t{r['chain_id']}\t"
                    f"{r['phase']}\t{r['deferral_reason']}\t{r['triage_mirror']}"
                )
    return EXIT_OK


def _cmd_show(args: argparse.Namespace, repo_root: pathlib.Path) -> int:
    found = queue_file.find_entry_across_files(repo_root, args.slug, args.task_id)
    if found is None:
        msg = f"no morning-review entry matches slug={args.slug} task_id={args.task_id}"
        if args.json_output:
            print(json.dumps({"error": "queue_entry_not_found", "message": msg}), file=sys.stderr)
        else:
            print(msg, file=sys.stderr)
        return EXIT_QUEUE_ENTRY_NOT_FOUND
    daily, entry = found
    if args.json_output:
        payload = {
            "task_id": entry.task_id,
            "status_since": entry.status_since,
            "slug": entry.slug,
            "chain_id": entry.chain_id,
            "phase": entry.phase,
            "deferral_reason": (
                entry.deferral_reason_token + " " + entry.deferral_reason_tail
            ).strip(),
            "retry_count": entry.retry_count,
            "verifier_verdict_ref": entry.verifier_verdict_ref,
            "verifier_reasoning": entry.verifier_reasoning,
            "triage_mirror": entry.triage_mirror,
            "daily_file": str(daily.relative_to(repo_root)),
        }
        print(json.dumps(payload, indent=2))
    else:
        deferral = entry.deferral_reason_token
        if entry.deferral_reason_tail:
            deferral = f"{deferral} {entry.deferral_reason_tail}"
        print(f"## {entry.task_id} (status: deferred since {entry.status_since})")
        print(f"**Plan:** {entry.slug}")
        print(f"**Chain ID:** {entry.chain_id}")
        print(f"**Phase:** {entry.phase}")
        print(f"**Deferral reason:** {deferral}")
        print(f"**Retry count when deferred:** {entry.retry_count}")
        print(f"**Verifier verdict ref:** {entry.verifier_verdict_ref}")
        print(f"**Operator triage:** {entry.triage_mirror}")
        print(f"-- daily file: {daily.relative_to(repo_root)}")
    return EXIT_OK


def _cmd_gc(args: argparse.Namespace, repo_root: pathlib.Path) -> int:
    try:
        moved, skipped = archive.gc(
            repo_root,
            older_than_days=args.older_than,
            dry_run=args.dry_run,
        )
    except OSError as exc:
        print(f"archive move failed (cross-fs fallback exhausted): {exc}", file=sys.stderr)
        return EXIT_ARCHIVE_MOVE_FAILED
    if args.json_output:
        print(
            json.dumps(
                {
                    "moved": [str(p.relative_to(repo_root)) for p in moved],
                    "skipped": [str(p.relative_to(repo_root)) for p in skipped],
                    "dry_run": args.dry_run,
                }
            )
        )
    else:
        if moved:
            print("moved:")
            for p in moved:
                print(f"  - {p.relative_to(repo_root)}")
        else:
            print("(nothing to move)")
        if skipped:
            print(f"skipped {len(skipped)} files")
    return EXIT_OK


# --- §J.impl.8 eval-extension surfaces ----------------------------------

def _cmd_mark_for_eval(args: argparse.Namespace, repo_root: pathlib.Path) -> int:
    return eval_extensions.mark_for_eval(
        repo_root=repo_root,
        failure_id=args.failure_id,
        labels=args.labels,
        case_id=args.case_id,
        reactivate_retired=args.reactivate_retired,
        dry_run=args.dry_run,
        json_output=args.json_output,
    )


def _cmd_label_score(args: argparse.Namespace, repo_root: pathlib.Path) -> int:
    return eval_extensions.label_score(
        repo_root=repo_root,
        score_id=args.score_id,
        label=args.label,
        source=args.source,
        notes=args.notes,
        dry_run=args.dry_run,
        json_output=args.json_output,
    )


def _cmd_retire_case(args: argparse.Namespace, repo_root: pathlib.Path) -> int:
    return eval_extensions.retire_case(
        repo_root=repo_root,
        case_id=args.case_id,
        reason=args.reason,
        dry_run=args.dry_run,
        json_output=args.json_output,
    )


# --- dispatch ------------------------------------------------------------

def dispatch(args: argparse.Namespace, *, repo_root: Optional[pathlib.Path] = None) -> int:
    repo = repo_root or _repo_root()

    # Internal-prefix subcommands first (they short-circuit normal subcommand
    # dispatch).
    if args.internal_bootstrap_day:
        if not args.g_slug:
            print(
                "--internal-bootstrap-day requires --slug <slug>", file=sys.stderr
            )
            return EXIT_USAGE
        return bootstrap.run(
            repo_root=repo,
            slug=args.g_slug,
            date_iso=args.internal_bootstrap_day,
        )
    if args.internal_mark_deferred:
        if not args.g_marker:
            print(
                "--internal-mark-deferred requires --marker <marker_id>",
                file=sys.stderr,
            )
            return EXIT_USAGE
        return mark_deferred.run(
            repo_root=repo,
            task_id=args.internal_mark_deferred,
            marker_id=args.g_marker,
            json_output=args.json_output,
        )

    # Subcommand dispatch.
    if args.subcommand is None:
        # No subcommand + no internal flags = print help + usage exit.
        cli.build_parser().print_help()
        return EXIT_USAGE
    if args.subcommand == "list":
        return _cmd_list(args, repo)
    if args.subcommand == "show":
        return _cmd_show(args, repo)
    if args.subcommand == "reactivate":
        return triage_dispatch.reactivate(
            repo_root=repo,
            slug=args.slug,
            task_id=args.task_id,
            reason=args.reason,
            dry_run=args.dry_run,
            json_output=args.json_output,
        )
    if args.subcommand == "route-outstanding":
        return triage_dispatch.route_outstanding(
            repo_root=repo,
            slug=args.slug,
            task_id=args.task_id,
            reason=args.reason,
            dry_run=args.dry_run,
            json_output=args.json_output,
        )
    if args.subcommand == "route-marker":
        return triage_dispatch.route_marker(
            repo_root=repo,
            slug=args.slug,
            task_id=args.task_id,
            prefix=args.prefix,
            reason=args.reason,
            trigger=args.trigger,
            detail=args.detail,
            dry_run=args.dry_run,
            json_output=args.json_output,
        )
    if args.subcommand == "abandon":
        refusal = _check_abandon_args(args)
        if refusal is not None:
            print(refusal, file=sys.stderr, end="")
            if args.dry_run:
                # Dry-run intent is to preview, not refuse.
                return EXIT_OK
            return EXIT_ABANDON_ARGS_MISSING
        return triage_dispatch.abandon(
            repo_root=repo,
            slug=args.slug,
            task_id=args.task_id,
            reason=args.reason,
            dry_run=args.dry_run,
            json_output=args.json_output,
        )
    if args.subcommand == "acknowledge":
        return triage_dispatch.acknowledge(
            repo_root=repo,
            slug=args.slug,
            json_output=args.json_output,
        )
    if args.subcommand == "gc":
        return _cmd_gc(args, repo)
    if args.subcommand == "mark-for-eval":
        return _cmd_mark_for_eval(args, repo)
    if args.subcommand == "label-score":
        return _cmd_label_score(args, repo)
    if args.subcommand == "retire-case":
        return _cmd_retire_case(args, repo)
    print(f"Unknown subcommand: {args.subcommand}", file=sys.stderr)
    return EXIT_USAGE


def main(argv: Optional[List[str]] = None) -> int:
    args = cli.parse_argv(argv)
    try:
        return dispatch(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
