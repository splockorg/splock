"""Argparse + subcommand dispatch for `bin/lessons`.

Per implplan §M.impl.5 CLI surface — three subcommands:

- ``add <slug>`` — required flags --task --title --approach --failure
  --rejection --reattempt --source; optional --dry-run.
- ``query <slug>`` — optional --task / --keyword / --json.
- ``list`` — optional --slug / --recent N / --json.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import EMIT_ADD, EMIT_LIST, EMIT_QUERY
from . import exit_codes
from .log_emit import emit_lesson_added, emit_lesson_queried
from .parser import LessonEntry, LessonsEntryMalformedError
from .query import PlanNotFoundError, list_lesson_files, query_lessons
from .validate import (
    MissingRequiredFieldError,
    SchemaValidationError,
    validate_or_raise,
    validate_schema,
)
from .writer import (
    AtomicWriteFailedError,
    append_lesson,
    render_entry,
    resolve_plan_dir,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin/lessons",
        description="Per-plan lessons.md CLI (splock implplan §M.impl.5).",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # --- add ----------------------------------------------------------------
    p_add = sub.add_parser("add", help="Append a lesson entry to lessons.md")
    p_add.add_argument("slug", help="Plan slug (e.g., brand_handoff_gate)")
    p_add.add_argument("--task", required=True, help="Task ID (T-prefixed integer; e.g., T3)")
    p_add.add_argument("--title", required=True, help="One-line failure title")
    p_add.add_argument("--approach", required=True, help="Approach attempted")
    p_add.add_argument(
        "--failure",
        required=True,
        metavar="failure_mode",
        help="Failure mode (stored as failure_mode in lessons_v1 schema)",
    )
    p_add.add_argument("--rejection", required=True, help="Why this approach was rejected")
    p_add.add_argument("--reattempt", required=True, help="Re-attempt criteria")
    p_add.add_argument("--source", required=True,
                       help="Source pointer (e.g., _orchestrator_log.jsonl:line=123)")
    p_add.add_argument("--date", default=None,
                       help="ISO-8601 date override (default: today UTC)")
    p_add.add_argument("--dry-run", action="store_true",
                       help="Print rendered entry to stdout; do not write")
    p_add.add_argument("--json", action="store_true", dest="json_output",
                       help="Emit JSON envelope on stdout")

    # --- query --------------------------------------------------------------
    p_query = sub.add_parser("query", help="Read + filter lessons.md entries")
    p_query.add_argument("slug", help="Plan slug")
    p_query.add_argument("--task", default=None, help="Filter by task ID")
    p_query.add_argument("--keyword", default=None,
                         help="Case-insensitive substring match across title/approach/failure_mode/rejection/reattempt")
    p_query.add_argument("--json", action="store_true", dest="json_output",
                         help="Emit JSON array on stdout")
    p_query.add_argument("--strict", action="store_true",
                         help="Raise on malformed entries instead of lenient-drop")

    # --- list ---------------------------------------------------------------
    p_list = sub.add_parser("list", help="Enumerate lessons.md files")
    p_list.add_argument("--slug", default=None, help="Filter by slug")
    p_list.add_argument("--recent", type=int, default=None,
                        help="Most-recent N by mtime")
    p_list.add_argument("--json", action="store_true", dest="json_output",
                        help="Emit JSON array on stdout")

    return parser


def _today_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def cmd_add(args: argparse.Namespace) -> int:
    arg_dict = {
        "task": args.task,
        "title": args.title,
        "approach": args.approach,
        "failure": args.failure,
        "rejection": args.rejection,
        "reattempt": args.reattempt,
        "source": args.source,
    }

    # Step 1: required-field check (exit 36 on missing).
    try:
        validate_or_raise(arg_dict)
    except MissingRequiredFieldError as exc:
        _emit_error(args.json_output, "lessons_required_field_missing",
                    str(exc), missing=exc.missing)
        return exit_codes.EXIT_LESSONS_REQUIRED_FIELD_MISSING

    # Step 2: build entry + schema-validate (exit 4 on schema rejection).
    date = args.date or _today_iso()
    entry = LessonEntry(
        date=date,
        title=args.title.strip(),
        task=args.task.strip(),
        approach=args.approach.strip(),
        failure_mode=args.failure.strip(),
        rejection=args.rejection.strip(),
        reattempt=args.reattempt.strip(),
        source=args.source.strip(),
    )
    try:
        validate_schema(entry)
    except SchemaValidationError as exc:
        _emit_error(args.json_output, "schema_rejected", str(exc),
                    path=exc.path)
        return exit_codes.EXIT_SCHEMA_REJECTED

    if args.dry_run:
        rendered = render_entry(entry)
        if args.json_output:
            print(json.dumps({"ok": True, "dry_run": True,
                              "rendered": rendered,
                              "entry": entry.to_dict()},
                             indent=2, sort_keys=True))
        else:
            print(rendered)
        return exit_codes.EXIT_OK

    # Step 3: atomic append (exit 7 on write failure).
    try:
        target = append_lesson(args.slug, entry)
    except AtomicWriteFailedError as exc:
        _emit_error(args.json_output, "atomic_write_failed", str(exc))
        return exit_codes.EXIT_ATOMIC_WRITE_FAILED

    # Step 4: log emission to §C JSONL (best-effort, swallows §C-absent).
    emit_lesson_added(args.slug, task_id=entry.task, title=entry.title)

    if args.json_output:
        print(json.dumps({"ok": True, "path": str(target),
                          "entry": entry.to_dict()},
                         indent=2, sort_keys=True))
    else:
        print(f"appended lesson {entry.task} to {target}")
    return exit_codes.EXIT_OK


def cmd_query(args: argparse.Namespace) -> int:
    try:
        entries = query_lessons(
            args.slug,
            task=args.task,
            keyword=args.keyword,
            strict=args.strict,
        )
    except PlanNotFoundError as exc:
        _emit_error(args.json_output, "plan_not_found", str(exc))
        return exit_codes.EXIT_PLAN_NOT_FOUND
    except LessonsEntryMalformedError as exc:
        _emit_error(args.json_output, "lessons_entry_malformed", str(exc))
        return exit_codes.EXIT_LESSONS_ENTRY_MALFORMED

    # Log emission (best-effort).
    emit_lesson_queried(
        args.slug, task=args.task, keyword=args.keyword,
        hits=len(entries),
    )

    if args.json_output:
        print(json.dumps([e.to_dict() for e in entries],
                         indent=2, sort_keys=True))
    else:
        if not entries:
            print(f"(no lessons matched for slug={args.slug})")
        for e in entries:
            print(f"## {e.date} — {e.title}")
            print(f"  Task: {e.task}")
            print(f"  Approach: {e.approach}")
            print(f"  Failure: {e.failure_mode}")
            print(f"  Source: {e.source}")
            print()
    return exit_codes.EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    rows = list_lesson_files(slug=args.slug, recent=args.recent)
    if args.json_output:
        print(json.dumps(rows, indent=2, sort_keys=True))
    else:
        if not rows:
            print("(no lessons.md files found)")
        for r in rows:
            print(f"{r['slug']:<40} entries={r['entries']:<3} {r['path']}")
    return exit_codes.EXIT_OK


def _emit_error(
    json_output: bool, code: str, detail: str, **extra: object
) -> None:
    payload = {"error": code, "detail": detail, **extra}
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True), file=sys.stderr)
    else:
        print(f"{code}: {detail}", file=sys.stderr)


def dispatch(args: argparse.Namespace) -> int:
    if args.subcommand == "add":
        return cmd_add(args)
    if args.subcommand == "query":
        return cmd_query(args)
    if args.subcommand == "list":
        return cmd_list(args)
    raise SystemExit(f"Unknown subcommand: {args.subcommand}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exit_codes.EXIT_USAGE if exc.code not in (0, None) else exit_codes.EXIT_OK
    try:
        return dispatch(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


__all__ = ["build_parser", "dispatch", "main"]
