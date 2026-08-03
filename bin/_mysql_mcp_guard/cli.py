"""CLI entry for `bin/mysql-mcp-guard`.

Subcommands:

  probe                 Verify a mysql MCP credential is read-only.
                        Default server: `mysql` (the subagent lane);
                        `--server <name>` probes another entry;
                        `--all` sweeps every mysql-prefixed server and
                        exits on the worst verdict.
                        Exit 0 inert/ok; 51 write-capable; 52 unverifiable.
                        `SPLOCK_MYSQL_MCP_GUARD=warn` downgrades 51/52 to a
                        stderr warning + exit 0; `off` skips entirely.
  statement --sql SQL   Grade one SQL string. Exit 0 clean / 51 write-shaped.

The /qna and /recon slash layers run `probe` as a spawn gate; the
PreToolUse hook (`hooks/mysql-mcp-guard.sh`) applies both layers per MCP
call, probing the specific server each call targets.
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


def _verdict_exit(verdict, mode: str, server: str) -> int:
    """Map one server's verdict to an exit code under the current mode."""
    if verdict.status in ("inert", "ok"):
        print(f"mysql-mcp-guard [{server}]: {verdict.status} — {verdict.detail}")
        return EXIT_OK
    if verdict.status == "write_capable":
        if mode == "warn":
            print(
                f"mysql-mcp-guard [{server}]: WARN (downgraded) — {verdict.detail}",
                file=sys.stderr,
            )
            return EXIT_OK
        print(
            f"mysql-mcp-guard [{server}]: REFUSE (exit {EXIT_WRITE_CAPABLE}) — "
            f"{verdict.detail}\n{_FIX_HINT}",
            file=sys.stderr,
        )
        return EXIT_WRITE_CAPABLE
    # unverifiable
    if mode == "warn":
        print(
            f"mysql-mcp-guard [{server}]: WARN (unverified) — {verdict.detail}",
            file=sys.stderr,
        )
        return EXIT_OK
    print(
        f"mysql-mcp-guard [{server}]: REFUSE (exit {EXIT_UNVERIFIABLE}) — "
        f"{verdict.detail}\n"
        "A gate that could not run is not a pass (VISION §4.7). "
        "Verify the credential manually, then either make the probe runnable "
        "(mysql client on PATH + resolvable creds) or set "
        "SPLOCK_MYSQL_MCP_GUARD=warn to accept unverified.",
        file=sys.stderr,
    )
    return EXIT_UNVERIFIABLE


def _run_probe(args: argparse.Namespace) -> int:
    mode = probe_mod.resolve_mode()
    if mode == "off":
        print("mysql-mcp-guard: mode=off — probe skipped")
        return EXIT_OK
    root = Path(args.project_root).resolve() if args.project_root else project_root()
    if args.all:
        servers = probe_mod.list_mysql_servers(root)
        if not servers:
            print("mysql-mcp-guard: inert — no mysql-prefixed MCP servers configured")
            return EXIT_OK
    else:
        servers = [args.server]
    # Worst-of across servers: write_capable outranks unverifiable outranks ok.
    worst = EXIT_OK
    for server in servers:
        verdict = probe_mod.probe(root, server=server, use_cache=not args.no_cache)
        rc = _verdict_exit(verdict, mode, server)
        if rc == EXIT_WRITE_CAPABLE or (rc == EXIT_UNVERIFIABLE and worst == EXIT_OK):
            worst = rc
    return worst


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

    p_probe = sub.add_parser("probe", help="verify a mysql MCP credential is read-only")
    p_probe.add_argument("--project-root", help="override adopter project root")
    p_probe.add_argument(
        "--no-cache", action="store_true", help="skip the 15-minute ok-verdict cache"
    )
    p_probe.add_argument(
        "--server",
        default="mysql",
        help="which mcpServers entry to probe (default: mysql — the subagent lane)",
    )
    p_probe.add_argument(
        "--all",
        action="store_true",
        help="probe every mysql-prefixed server; exit reflects the worst verdict",
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
