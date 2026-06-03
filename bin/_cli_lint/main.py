"""`bin/cli-lint` Python entry — catalog + standing-requirements static check.

Per implplan §N.impl.3. POSIX-shell wrapper at `bin/cli-lint`
dispatches to `python -m bin._cli_lint.main`.

CLI shape:
  bin/cli-lint --check [--all]              # Validate every catalog CLI
  bin/cli-lint --check --cli <name>         # Validate single CLI
  bin/cli-lint --check --changed-only       # Validate CLIs changed in HEAD
  bin/cli-lint --list-rules                 # Print the 6 standing rules

Exit codes (closed enum; §A.impl.3a + §N.impl.9 #2):
  0  = all rules pass
  1  = usage
  2  = driver crash (catalog missing/malformed)
  38 = cli_lint_violation (one or more rules failed; rule discriminated
       in structured stderr)

Structured stderr on violation (one JSON object per line):
  {"error":"cli_lint_violation","rule":"REQ_X_<name>","cli":"bin/<name>",
   "line":<N>,"detail":"<text>"}
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from bin._cli_lint.catalog_parser import (
    CATALOG_PATH,
    REPO_ROOT,
    parse_catalog,
)
from bin._cli_lint.check import lint_catalog
from bin._cli_lint.exit_codes import (
    EXIT_CLI_LINT_VIOLATION,
    EXIT_DRIVER_CRASH,
    EXIT_OK,
    EXIT_USAGE,
)
from bin._cli_lint.rules import RULES, Violation


def _list_rules() -> int:
    sys.stdout.write("cli-lint rules:\n")
    for rule_id, statement, _fn in RULES:
        sys.stdout.write(f"  {rule_id}: {statement}\n")
    sys.stdout.write(
        f"\nAll violations exit with code {EXIT_CLI_LINT_VIOLATION} "
        f"(cli_lint_violation per A.impl.3a).\n"
    )
    sys.stdout.write(
        "Rule is discriminated in structured stderr "
        '{"error":"cli_lint_violation","rule":"...","cli":"...","detail":"..."}.\n'
    )
    return EXIT_OK


def _changed_clis() -> list[str]:
    """Return the list of `bin/<name>` paths changed in the most recent
    commit (or HEAD vs. index for pre-commit usage)."""
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
    except FileNotFoundError:
        return []
    files = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    return [f for f in files if f.startswith("bin/") and "/" not in f[4:]]


def _emit_violation(v: Violation) -> None:
    payload = {
        "error": "cli_lint_violation",
        "rule": v.rule,
        "cli": v.cli,
        "line": v.line,
        "detail": v.detail,
    }
    sys.stderr.write(json.dumps(payload, sort_keys=True) + "\n")


def cmd_check(
    cli_filter: str | None,
    changed_only: bool,
) -> int:
    try:
        catalog = parse_catalog()
    except FileNotFoundError as e:
        sys.stderr.write(
            json.dumps({
                "error": "catalog_not_found",
                "detail": str(e),
            }) + "\n"
        )
        return EXIT_DRIVER_CRASH
    except ValueError as e:
        sys.stderr.write(
            json.dumps({
                "error": "catalog_malformed",
                "detail": str(e),
            }) + "\n"
        )
        return EXIT_DRIVER_CRASH

    only_cli: str | None = None
    if cli_filter is not None:
        # Normalize to "bin/<name>".
        norm = cli_filter if cli_filter.startswith("bin/") else f"bin/{cli_filter}"
        only_cli = norm
        # Check existence in catalog.
        catalog_names = {e.cli for e in catalog}
        if norm not in catalog_names:
            sys.stderr.write(
                json.dumps({
                    "error": "cli_not_in_catalog",
                    "cli": norm,
                }) + "\n"
            )
            return EXIT_USAGE

    if changed_only:
        changed = set(_changed_clis())
        if not changed:
            sys.stdout.write("cli-lint: no bin/* files changed; nothing to check\n")
            return EXIT_OK
        # Restrict the catalog to the changed set.
        filtered = [e for e in catalog if e.cli in changed]
        if not filtered:
            sys.stdout.write(
                "cli-lint: changed bin/* files are not in catalog; "
                "nothing to check\n"
            )
            return EXIT_OK
        violations: list[Violation] = []
        for entry in filtered:
            violations.extend(lint_catalog([entry], only_cli=entry.cli))
    else:
        violations = lint_catalog(catalog, only_cli=only_cli)

    if not violations:
        scope = only_cli or ("changed CLIs" if changed_only else f"{len(catalog)} CLIs")
        sys.stdout.write(f"cli-lint: PASS ({scope})\n")
        return EXIT_OK
    for v in violations:
        _emit_violation(v)
    return EXIT_CLI_LINT_VIOLATION


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin/cli-lint",
        description=(
            "Catalog + standing-requirements static check. "
            "See docs/cli_tooling_catalog.md and implplan §N.impl.3."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("--check", action="store_true",
                        help="Validate CLIs (default scope: every catalog entry)")
    parser.add_argument("--all", action="store_true",
                        help="Explicit alias for `--check` with no filter")
    parser.add_argument("--cli", default=None,
                        help="Single CLI name to validate (e.g., bin/marker)")
    parser.add_argument("--changed-only", action="store_true",
                        help="Validate only CLIs changed in HEAD")
    parser.add_argument("--list-rules", action="store_true",
                        help="Print the closed-enum rule set")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_rules:
        return _list_rules()

    if args.check or args.all:
        return cmd_check(cli_filter=args.cli, changed_only=args.changed_only)

    parser.print_help(sys.stderr)
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
