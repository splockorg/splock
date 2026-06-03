"""argparse surface for `bin/intent` (implplan §P.impl.3).

Seven subcommands per the table at lines 8478-8487. Exact flag set
matched against the implplan.
"""

from __future__ import annotations

import argparse
from typing import List, Optional


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin/intent",
        description=(
            "Agent-session intent registry. See docs/plans/splock/"
            "splock_implplan.md §P.impl."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="emit machine-readable stdout",
    )
    parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="compute + print intended write; no I/O",
    )

    sub = parser.add_subparsers(dest="subcommand", required=True)

    # --- check -----------------------------------------------------------
    p_check = sub.add_parser("check", help="Read-only collision query")
    p_check.add_argument("--area", required=True, help="target_system_area to query")
    p_check.add_argument(
        "--paths", default=None,
        help="comma-separated glob list to additionally check overlap on",
    )
    p_check.add_argument("--host", default=None, help="restrict to a specific host")

    # --- register --------------------------------------------------------
    p_reg = sub.add_parser("register", help="Register a new session intent")
    p_reg.add_argument("--area", required=True)
    p_reg.add_argument(
        "--paths", required=True,
        help="comma-separated glob list (e.g. 'extraction/grouper_pipeline/**')",
    )
    p_reg.add_argument(
        "--kind", required=True,
        help="closed enum: interactive | chain_overnight | read_only_recon | read_only_review",
    )
    p_reg.add_argument(
        "--closure", default=None,
        help=(
            "closure trigger: pr_merged:<branch> | commits_landed:<sha_range> | "
            "session_timeout:<NUM{m,h,d}> | manual_complete  (default: "
            "session_timeout:<intent.ttl_minutes>m)"
        ),
    )
    p_reg.add_argument(
        "--design-pattern", default=None, dest="design_pattern",
        help="optional proposed_design_pattern free text",
    )
    p_reg.add_argument("--plan", default=None, help="originating plan slug")
    p_reg.add_argument(
        "--chain-id", default=None, dest="chain_id",
        help="originating chain ID (auto-detected from $SPLOCK_CHAIN_ID otherwise)",
    )
    p_reg.add_argument(
        "--emitted-by", default="bin/intent:register", dest="emitted_by",
        choices=[
            "bin/intent",
            "bin/intent:register",
            "chain_driver_auto",
            # T2 (intent_session_auto_register): permitted external value
            # stamped by the SessionStart hook's subprocess-call. Threads
            # through refusal.EMITTED_BY + writers.KNOWN_WRITERS v6.
            "session_start_auto",
            "user_prompt_submit_auto",
        ],
    )
    # T1 (intent_session_auto_register): side-column capture of Claude
    # Code session_id from the SessionStart hook envelope. NULL when
    # omitted (chain-overnight + pre-T1 rows). Per research Decision 1:
    # not a PK — the registry session_id stays `new_session_id()`.
    p_reg.add_argument(
        "--claude-session-id", default=None, dest="claude_session_id",
        help=(
            "Claude Code session_id from the SessionStart hook envelope; "
            "stored as a side column for /clear-recovery lookups via "
            "`bin/intent list --claude-session <id>`"
        ),
    )
    # UserPromptSubmit hook needs an idempotent variant: if an open row
    # already exists for `--claude-session-id`, bump last_activity_at and
    # exit without inserting a new row. This prevents row spam from
    # firing on every prompt.
    p_reg.add_argument(
        "--upsert", action="store_true",
        help=(
            "Idempotent register. Requires --claude-session-id. If an open "
            "row exists for that Claude session, UPDATE last_activity_at "
            "and exit 0; else fall through to normal insert."
        ),
    )

    # --- update ----------------------------------------------------------
    p_upd = sub.add_parser("update", help="Mutate session fields")
    p_upd.add_argument("session_id")
    p_upd.add_argument("--paths", default=None)
    p_upd.add_argument("--status", default=None,
                       help="closed enum: Planning|Coding|Reviewing|Blocked|Paused|Done")
    p_upd.add_argument("--note", default=None)

    # --- complete --------------------------------------------------------
    p_cmp = sub.add_parser("complete", help="Mark session terminal")
    p_cmp.add_argument("session_id")
    p_cmp.add_argument("--reason", default=None)

    # --- list ------------------------------------------------------------
    p_ls = sub.add_parser("list", help="List sessions")
    p_ls.add_argument("--area", default=None)
    p_ls.add_argument("--kind", default=None)
    p_ls.add_argument("--host", default=None)
    # T1 (intent_session_auto_register): filter rows by Claude session_id;
    # used to trace /clear continuations of the same upstream Claude
    # session across multiple registry rows.
    p_ls.add_argument(
        "--claude-session", default=None, dest="claude_session",
        help=(
            "filter rows whose claude_session_id matches the supplied "
            "Claude Code session_id (T1 — intent_session_auto_register)"
        ),
    )
    grp = p_ls.add_mutually_exclusive_group()
    grp.add_argument("--active", action="store_true", default=False)
    grp.add_argument("--closed", action="store_true", default=False)
    grp.add_argument("--all", action="store_true", default=False, dest="all_sessions")

    # --- pivot -----------------------------------------------------------
    p_piv = sub.add_parser("pivot", help="Move session to a new area")
    p_piv.add_argument("session_id")
    p_piv.add_argument("--area", required=True, dest="new_area")
    p_piv.add_argument("--paths", default=None)

    # --- doctor ----------------------------------------------------------
    p_doc = sub.add_parser("doctor", help="Run closure-trigger sweep + sync reconciliation")
    p_doc.add_argument("--reconcile-sync-pending", action="store_true",
                       default=True, dest="reconcile_sync_pending")
    p_doc.add_argument("--no-reconcile", action="store_false",
                       dest="reconcile_sync_pending",
                       help="skip sync_pending reconciliation pass")

    # Repeat global flags on every subparser so they can appear after the
    # subcommand name (argparse limitation: global flags only work before
    # the subcommand). Each parser owns its own `--dry-run` / `--json`
    # attribute; main.py reads the value with sensible fallbacks.
    for sp in (p_check, p_reg, p_upd, p_cmp, p_ls, p_piv, p_doc):
        sp.add_argument("--json", action="store_true", dest="json_output")
        sp.add_argument("--dry-run", action="store_true", dest="dry_run")

    return parser


def parse_paths(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


__all__ = ["build_parser", "parse_paths"]
