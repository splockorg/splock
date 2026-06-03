"""CLI entry point for `bin/marker` (implplan §K.impl.3).

Six subcommands (per §K.impl.3 table):
  - create <PREFIX> "<title>" --trigger <spec> [opts]
  - close <ID> --resolution "<text>" [opts]
  - list [--prefix P] [--source-plan slug] [--active|--closed|--all]
  - show <ID> [--detail-only]
  - validate [--all|--changed-only] [--strict-edit-block]
  - register-prefix <NEW_PREFIX> --domain "<text>" --owner "<text>"
       [--examples "<csv>"] [--dry-run]

Global flags: --dry-run, --allow-na, --json.

Invoked via the POSIX shell wrapper at `bin/marker` which delegates to
`python -m bin._marker.main`.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin/marker",
        description=(
            "Scheduled-marker CLI. See docs/plans/splock/splock_implplan.md "
            "§K.impl + docs/plans/scheduled_markers/prefix_registry.md."
        ),
    )
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="emit machine-readable stdout")

    sub = parser.add_subparsers(dest="subcommand", required=True)

    # --- create -----------------------------------------------------------------
    p_create = sub.add_parser("create", help="Mint a new marker entry")
    p_create.add_argument("prefix", help="Marker prefix (e.g., INV, ANC, CTM)")
    p_create.add_argument("title", help="One-line title (1-200 chars; imperative shape preferred)")
    p_create.add_argument("--trigger", required=False, default=None,
                          help="Trigger spec: edit:<path>:<shape> | date:YYYY-MM-DD | condition:<spec>")
    p_create.add_argument("--plan", default=None, help="Plan slug (or omit for cross-cutting)")
    p_create.add_argument("--module", default=None, help="Affected module / subsystem")
    p_create.add_argument("--data-needed", default=None,
                          help="What evidence/state must exist before closure")
    p_create.add_argument("--context", default=None, help="One-paragraph context")
    p_create.add_argument("--detail", default=None,
                          help="Edit-block content (literal text or path to file)")
    p_create.add_argument("--emitted-by", default="bin/marker",
                          choices=[
                              "bin/marker",
                              "bin/morning-review:route-marker",
                              "bin/route_issue:route-marker",
                              "agent",
                          ],
                          help="Stamps the marker-entry emitted_by field (§K.impl.3 enum)")
    p_create.add_argument("--allow-na", action="store_true",
                          help="Authorize `data_needed=n/a` (otherwise refused)")
    p_create.add_argument("--dry-run", action="store_true",
                          help="Compute + print diff, no write")

    # --- close -----------------------------------------------------------------
    p_close = sub.add_parser("close", help="Close an active marker")
    p_close.add_argument("marker_id", help="Marker ID (e.g., INV.13)")
    p_close.add_argument("--resolution", required=True,
                         help="One-line resolution summary")
    p_close.add_argument("--dry-run", action="store_true")

    # --- list ------------------------------------------------------------------
    p_list = sub.add_parser("list", help="List marker entries")
    p_list.add_argument("--prefix", default=None, help="Filter by prefix")
    p_list.add_argument("--source-plan", default=None, dest="source_plan",
                        help="Filter by source plan slug")
    p_group = p_list.add_mutually_exclusive_group()
    p_group.add_argument("--active", action="store_true", default=False,
                         help="Show active entries (default)")
    p_group.add_argument("--closed", action="store_true", default=False,
                         help="Show closed entries only")
    p_group.add_argument("--all", action="store_true", default=False, dest="all_entries",
                         help="Show both active and closed")

    # --- show ------------------------------------------------------------------
    p_show = sub.add_parser("show", help="Print entry + detail file content")
    p_show.add_argument("marker_id", help="Marker ID")
    p_show.add_argument("--detail-only", action="store_true",
                        help="Print only the detail file content")

    # --- validate --------------------------------------------------------------
    p_validate = sub.add_parser("validate", help="CI / pre-commit gate")
    p_v_group = p_validate.add_mutually_exclusive_group()
    p_v_group.add_argument("--all", action="store_true", default=True,
                           help="Scan every entry (default)")
    p_v_group.add_argument("--changed-only", action="store_true", default=False,
                           help="Only validate entries touched by staged diff")
    p_validate.add_argument("--strict-edit-block", action="store_true",
                            help="Refuse on missing edit-block even for legacy entries")

    # --- register-prefix -------------------------------------------------------
    p_reg = sub.add_parser("register-prefix", help="Register a new prefix in prefix_registry.md")
    p_reg.add_argument("new_prefix", help="New prefix (3-5 uppercase letters)")
    p_reg.add_argument("--domain", required=True, help="One-line domain description")
    p_reg.add_argument("--owner", required=True, help="Owner module / area")
    p_reg.add_argument("--examples", default="", help="Comma-separated initial marker IDs")
    p_reg.add_argument("--dry-run", action="store_true")

    return parser


def dispatch(args: argparse.Namespace) -> int:
    if args.subcommand == "create":
        from . import create
        return create.run(
            prefix=args.prefix,
            title=args.title,
            trigger=args.trigger,
            plan=args.plan,
            module=args.module,
            data_needed=args.data_needed,
            context=args.context,
            detail=args.detail,
            allow_na=args.allow_na,
            dry_run=args.dry_run,
            emitted_by=args.emitted_by,
            json_output=args.json_output,
        )
    if args.subcommand == "close":
        from . import close
        return close.run(
            marker_id=args.marker_id,
            resolution=args.resolution,
            dry_run=args.dry_run,
            json_output=args.json_output,
        )
    if args.subcommand == "list":
        from . import list_cmd
        return list_cmd.run(
            prefix_filter=args.prefix,
            source_plan=args.source_plan,
            active=args.active or not (args.closed or args.all_entries),
            closed=args.closed,
            all_entries=args.all_entries,
            json_output=args.json_output,
        )
    if args.subcommand == "show":
        from . import show
        return show.run(
            marker_id=args.marker_id,
            detail_only=args.detail_only,
            json_output=args.json_output,
        )
    if args.subcommand == "validate":
        from . import validate
        scope = "changed-only" if args.changed_only else "all"
        return validate.run(
            scope=scope,
            strict_edit_block=args.strict_edit_block,
            json_output=args.json_output,
        )
    if args.subcommand == "register-prefix":
        from . import register_prefix
        return register_prefix.run(
            new_prefix=args.new_prefix,
            domain=args.domain,
            owner=args.owner,
            examples=args.examples,
            dry_run=args.dry_run,
            json_output=args.json_output,
        )
    raise SystemExit(f"Unknown subcommand: {args.subcommand}")


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
