"""Argparse surface for `bin/route_issue` (implplan §L.impl.3).

Surface shape (per L.impl.3 table):

  bin/route_issue --type fix-now --description "<gloss>" --context "<ref>"
  bin/route_issue --type outstanding --description "<gloss>" --context "<ref>"
                  [--blast-radius <int>] [--related <line-id-csv>]
  bin/route_issue --type marker --prefix <P> --trigger <spec> --context "<ref>"
  bin/route_issue --type tier-promote --slug <slug> --context "<line_id>"
  bin/route_issue --type escalate --reason "<text>" --context "<ref>"
                  [--trigger-source <enum>]
  bin/route_issue --check-scope [--include-ddl-count]
                                [--check-override-state]
                                [--allow-multi-ddl]

Global flags: --dry-run, --json.
"""

from __future__ import annotations

import argparse
from typing import List, Optional


VALID_TYPES = ("fix-now", "outstanding", "marker", "tier-promote", "escalate")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin/route_issue",
        description=(
            "Deferred-work routing CLI. See "
            "docs/plans/splock/splock_implplan.md §L.impl."
        ),
    )
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="emit machine-readable stdout")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="compute + print diff, no write")
    parser.add_argument("--slug", default=None,
                        help="plan slug (operator-supplied; per L.impl.12 #4)")

    # --check-scope meta surface (mutually exclusive with --type)
    parser.add_argument("--check-scope", action="store_true", dest="check_scope",
                        help="pre-commit hook entry point — evaluate escalation triggers")
    parser.add_argument("--include-ddl-count", action="store_true",
                        dest="include_ddl_count",
                        help="(--check-scope) also detect multi-column DDL via safe-ddl log")
    parser.add_argument("--check-override-state", action="store_true",
                        dest="check_override_state",
                        help="(--check-scope) detect pending operator-override transitions")
    parser.add_argument("--allow-multi-ddl", action="store_true",
                        dest="allow_multi_ddl",
                        help="(--check-scope) operator override for DDL trigger")

    # --type dispatch
    parser.add_argument("--type", choices=VALID_TYPES, default=None, dest="type_",
                        help="routing type")

    # fix-now / outstanding fields
    parser.add_argument("--description", default=None,
                        help="(fix-now / outstanding) one-line gloss")

    # outstanding-specific
    parser.add_argument("--blast-radius", type=int, default=None,
                        dest="blast_radius",
                        help="(outstanding) estimated blast radius integer")
    parser.add_argument("--related", default=None,
                        help="(outstanding) CSV of related line IDs")

    # marker-specific
    parser.add_argument("--prefix", default=None,
                        help="(marker) marker prefix (e.g., INV, ANC)")
    parser.add_argument("--trigger", default=None,
                        help="(marker) trigger spec passed to bin/marker create")
    parser.add_argument("--marker-title", default=None, dest="marker_title",
                        help="(marker) optional title; falls back to --context")
    parser.add_argument("--plan", default=None,
                        help="(marker) source plan slug for the marker")
    parser.add_argument("--module", default=None,
                        help="(marker) affected module / subsystem")
    parser.add_argument("--data-needed", default=None, dest="data_needed",
                        help="(marker) closure evidence requirement")
    parser.add_argument("--detail", default=None,
                        help="(marker) detail-file content (literal or path)")
    parser.add_argument("--allow-na", action="store_true", dest="allow_na",
                        help="(marker) authorize data_needed=n/a")

    # tier-promote / escalate / shared
    parser.add_argument("--context", default=None,
                        help="(all --type) reference: task_id:phase, line_id, etc.")
    parser.add_argument("--reason", default=None,
                        help="(escalate) free-text reason")
    parser.add_argument("--trigger-source", default=None, dest="trigger_source",
                        choices=(
                            "blast_radius", "ddl_multi", "cross_vertical",
                            "cross_repo", "operator_override_state",
                            "operator_direct", "rubric_refused",
                        ),
                        help="(escalate) closed-enum trigger source attribution")

    return parser


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)
