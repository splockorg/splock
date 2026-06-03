"""CLI entry point for `bin/eval-baseline` (§J.impl.7)."""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Optional

from . import mint as mint_module
from .exit_codes import EXIT_OK, EXIT_USAGE


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bin/eval-baseline",
        description="Mint or list baselines for a plan slug.",
    )
    p.add_argument("slug")
    p.add_argument("--mint", dest="mint_name", default=None)
    p.add_argument("--list", action="store_true", dest="list_only")
    p.add_argument("--notes", default=None)
    p.add_argument("--json", action="store_true", dest="json_output")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = _repo_root()
    plan_dir = repo_root / "docs" / "plans" / args.slug
    if not plan_dir.exists():
        print(f"plan_dir does not exist: {plan_dir}", file=sys.stderr)
        return EXIT_USAGE

    if args.list_only:
        from .manifest import list_baselines

        for name in list_baselines(plan_dir):
            print(name)
        return EXIT_OK

    if args.mint_name:
        return mint_module.mint(
            plan_dir,
            name=args.mint_name,
            repo_root=repo_root,
            notes_text=args.notes,
        )

    _build_parser().print_help()
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
