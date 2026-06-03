"""argparse subcommand surface for `bin/morning-review` (implplan §H.impl.3).

Public subcommands:
  list / show / reactivate / route-outstanding / route-marker / abandon /
  acknowledge / gc / mark-for-eval / label-score / retire-case

Internal-prefix subcommands (hidden from --help):
  --internal-bootstrap-day / --internal-mark-deferred

Per §H.impl.8: `abandon` enforces `--confirm` + non-empty `--reason`
(exit 31 EXIT_ABANDON_ARGS_MISSING). Other approval-discipline rules
live here as well.
"""

from __future__ import annotations

import argparse
from typing import List, Optional


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin/morning-review",
        description=(
            "Morning-review queue UX. See "
            "docs/plans/splock/splock_implplan.md §H.impl."
        ),
    )
    # Global flags.
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="emit machine-readable stdout / stderr payloads",
    )
    # Internal-prefix subcommands (hidden — operate at the top-level flag
    # layer rather than via subparsers so they can be conditioned via
    # SUPPRESS-help):
    parser.add_argument(
        "--internal-bootstrap-day",
        dest="internal_bootstrap_day",
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--internal-mark-deferred",
        dest="internal_mark_deferred",
        default=None,
        help=argparse.SUPPRESS,
    )
    # Args consumed only by the internal modes; harmless on others.
    parser.add_argument("--slug", dest="g_slug", default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--marker", dest="g_marker", default=None, help=argparse.SUPPRESS
    )

    sub = parser.add_subparsers(dest="subcommand", required=False)

    # --- list ----------------------------------------------------------------
    p_list = sub.add_parser("list", help="List entries (default: unresolved only)")
    p_list.add_argument("--slug", default=None, help="Filter by slug")
    p_list_group = p_list.add_mutually_exclusive_group()
    p_list_group.add_argument(
        "--unresolved", action="store_true", default=False,
        help="Show only unresolved (mirror [pending]) entries (default)",
    )
    p_list_group.add_argument(
        "--all", action="store_true", default=False, dest="all_entries",
        help="Show every entry across every open daily file",
    )

    # --- show ----------------------------------------------------------------
    p_show = sub.add_parser("show", help="Show one entry by task_id")
    p_show.add_argument("slug")
    p_show.add_argument("task_id")

    # --- reactivate ----------------------------------------------------------
    p_react = sub.add_parser(
        "reactivate",
        help=(
            "Set task status back to wip. "
            "Note: this does NOT schedule the chain to resume. "
            "Use `bin/chain-overnight 5 <slug> --from-resume <chain_id>` "
            "to authorize the next chain run. (See plan §H.3a paragraph 2.)"
        ),
    )
    p_react.add_argument("slug")
    p_react.add_argument("task_id")
    p_react.add_argument("--reason", default=None)
    p_react.add_argument("--dry-run", action="store_true", dest="dry_run")

    # --- route-outstanding ---------------------------------------------------
    p_ro = sub.add_parser(
        "route-outstanding",
        help="Route this task to outstanding_issues.md via bin/route_issue",
    )
    p_ro.add_argument("slug")
    p_ro.add_argument("task_id")
    p_ro.add_argument("--reason", default=None)
    p_ro.add_argument("--dry-run", action="store_true", dest="dry_run")

    # --- route-marker --------------------------------------------------------
    p_rm = sub.add_parser(
        "route-marker",
        help="Route this task to scheduled_markers/ via bin/marker create",
    )
    p_rm.add_argument("slug")
    p_rm.add_argument("task_id")
    p_rm.add_argument("--prefix", required=True, help="Marker prefix (e.g., INV)")
    p_rm.add_argument("--reason", default=None)
    p_rm.add_argument("--trigger", default=None, help="Optional structured trigger spec")
    p_rm.add_argument("--detail", default=None, help="Optional path to edit-block content")
    p_rm.add_argument("--dry-run", action="store_true", dest="dry_run")

    # --- abandon -------------------------------------------------------------
    p_ab = sub.add_parser(
        "abandon",
        help=(
            "Abandon the task. Requires --confirm AND --reason "
            "(per plan §H.3a + §H.impl.8)."
        ),
    )
    p_ab.add_argument("slug")
    p_ab.add_argument("task_id")
    p_ab.add_argument(
        "--confirm", action="store_true",
        help="REQUIRED — explicit per-action approval flag",
    )
    p_ab.add_argument("--reason", default="", help="REQUIRED — non-empty reason")
    p_ab.add_argument("--dry-run", action="store_true", dest="dry_run")

    # --- acknowledge ---------------------------------------------------------
    p_ack = sub.add_parser(
        "acknowledge",
        help="Acknowledge the morning-review queue (clears cap-hit banner)",
    )
    p_ack.add_argument("slug")

    # --- gc ------------------------------------------------------------------
    p_gc = sub.add_parser(
        "gc",
        help="Archive older daily files whose entries are all terminal",
    )
    p_gc.add_argument(
        "--older-than", type=int, default=30, dest="older_than",
        help="Days threshold (default: 30)",
    )
    p_gc.add_argument("--dry-run", action="store_true", dest="dry_run")

    # --- v1.4 eval-extension stubs (per §H.impl.3) --------------------------
    p_mfe = sub.add_parser(
        "mark-for-eval", help="Promote a failure_id to regression case (§J.impl.6)"
    )
    p_mfe.add_argument("failure_id")
    p_mfe.add_argument("--labels", default=None)
    p_mfe.add_argument("--case-id", default=None, dest="case_id")
    p_mfe.add_argument("--reactivate-retired", action="store_true", dest="reactivate_retired")
    p_mfe.add_argument("--dry-run", action="store_true", dest="dry_run")

    p_ls = sub.add_parser("label-score", help="Append a label to a score (§J.impl.8)")
    p_ls.add_argument("score_id")
    p_ls.add_argument(
        "label",
        choices=["true-positive", "false-positive", "true-negative", "false-negative", "n/a"],
    )
    p_ls.add_argument("--source", default="operator_morning_review")
    p_ls.add_argument("--notes", default=None)
    p_ls.add_argument("--dry-run", action="store_true", dest="dry_run")

    p_rc = sub.add_parser("retire-case", help="Retire a regression case (§J.impl.8)")
    p_rc.add_argument("case_id")
    p_rc.add_argument("--reason", required=True)
    p_rc.add_argument("--dry-run", action="store_true", dest="dry_run")

    return parser


def parse_argv(argv: Optional[List[str]] = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)
