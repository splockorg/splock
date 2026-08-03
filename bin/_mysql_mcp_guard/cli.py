"""CLI entry for `bin/mysql-mcp-guard`.

Subcommands:

  probe                 Verify the `mysql` MCP credential is read-only.
                        Exit 0 inert/ok; 51 write-capable; 52 unverifiable.
                        `SPLOCK_MYSQL_MCP_GUARD=warn` downgrades 51/52 to a
                        stderr warning + exit 0; `off` skips entirely.
  statement --sql SQL   Grade one SQL string. Exit 0 clean / 51 write-shaped.

The /qna slash layer runs `probe` as a spawn gate; the PreToolUse hook
(`hooks/mysql-mcp-guard.sh`) applies both layers per MCP call.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bin._env_paths import project_root
from bin._mysql_mcp_guard import probe as probe_mod
from bin._mysql_mcp_guard import statement
from bin._mysql_mcp_guard.exit_codes import (
    EXIT_OK,
    EXIT_UNVERIFIABLE,
    EXIT_USAGE,
    EXIT_WRITE_CAPABLE,
)

_FIX_HINT = (
    "Fix: give the `mysql` MCP server a SELECT-only user, e.g.\n"
    "  CREATE USER 'splock_ro'@'%' IDENTIFIED BY '...';\n"
    "  GRANT SELECT, SHOW VIEW ON your_db.* TO 'splock_ro'@'%';\n"
    "then update the server env in .mcp.json / .env. "
    "Downgrade (not recommended): SPLOCK_MYSQL_MCP_GUARD=warn"
)


def _run_probe(args: argparse.Namespace) -> int:
    mode = probe_mod.resolve_mode()
    if mode == "off":
        print("mysql-mcp-guard: mode=off — probe skipped")
        return EXIT_OK
    root = Path(args.project_root).resolve() if args.project_root else project_root()
    verdict = probe_mod.probe(root, use_cache=not args.no_cache)
    if verdict.status in ("inert", "ok"):
        print(f"mysql-mcp-guard: {verdict.status} — {verdict.detail}")
        return EXIT_OK
    if verdict.status == "write_capable":
        message = (
            f"mysql-mcp-guard: REFUSE (exit {EXIT_WRITE_CAPABLE}) — "
            f"{verdict.detail}\n{_FIX_HINT}"
        )
        if mode == "warn":
            print(f"mysql-mcp-guard: WARN (downgraded) — {verdict.detail}", file=sys.stderr)
            return EXIT_OK
        print(message, file=sys.stderr)
        return EXIT_WRITE_CAPABLE
    # unverifiable
    message = (
        f"mysql-mcp-guard: REFUSE (exit {EXIT_UNVERIFIABLE}) — {verdict.detail}\n"
        "A gate that could not run is not a pass (VISION §4.7). "
        "Verify the credential manually, then either make the probe runnable "
        "(mysql client on PATH + resolvable creds) or set "
        "SPLOCK_MYSQL_MCP_GUARD=warn to accept unverified."
    )
    if mode == "warn":
        print(f"mysql-mcp-guard: WARN (unverified) — {verdict.detail}", file=sys.stderr)
        return EXIT_OK
    print(message, file=sys.stderr)
    return EXIT_UNVERIFIABLE


def _run_statement(args: argparse.Namespace) -> int:
    reason = statement.check_sql(args.sql)
    if reason:
        print(f"mysql-mcp-guard: write-shaped — {reason}", file=sys.stderr)
        return EXIT_WRITE_CAPABLE
    print("mysql-mcp-guard: statement clean")
    return EXIT_OK


def main(argv: list = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mysql-mcp-guard",
        description="Read-only gate for the `mysql` MCP surface.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_probe = sub.add_parser("probe", help="verify the mysql MCP credential is read-only")
    p_probe.add_argument("--project-root", help="override adopter project root")
    p_probe.add_argument(
        "--no-cache", action="store_true", help="skip the 15-minute ok-verdict cache"
    )
    p_probe.set_defaults(func=_run_probe)

    p_stmt = sub.add_parser("statement", help="grade one SQL string")
    p_stmt.add_argument("--sql", required=True, help="SQL text to grade")
    p_stmt.set_defaults(func=_run_statement)

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code not in (0, None) else 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
