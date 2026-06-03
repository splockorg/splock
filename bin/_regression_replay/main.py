"""CLI entry point for `bin/regression-replay` (§J.impl.6)."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Optional

from bin._eval_common import regression_case

from . import diff_render, replay_one
from .exit_codes import EXIT_CASE_NOT_FOUND, EXIT_OK, EXIT_USAGE


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bin/regression-replay",
        description="Replay a regression case for operator review.",
    )
    p.add_argument("slug")
    p.add_argument("case_id")
    p.add_argument("--json", action="store_true", dest="json_output")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    plan_dir = _repo_root() / "docs" / "plans" / args.slug
    if not plan_dir.exists():
        print(f"plan_dir does not exist: {plan_dir}", file=sys.stderr)
        return EXIT_USAGE

    case = regression_case.read_case(plan_dir, args.case_id)
    if case is None:
        print(f"case not found: {args.case_id}", file=sys.stderr)
        return EXIT_CASE_NOT_FOUND

    plan = replay_one.materialize(case)
    rendered = diff_render.render_side_by_side(
        case_id=plan.case_id,
        expected_outcome=plan.expected_outcome,
        expected_outcome_details=plan.expected_outcome_details,
        materialized_files=plan.materialized_files,
    )

    if args.json_output:
        print(
            json.dumps(
                {
                    "case_id": plan.case_id,
                    "tempdir": str(plan.tempdir),
                    "materialized": [str(p) for p in plan.materialized_files],
                    "expected_outcome": plan.expected_outcome,
                }
            )
        )
    else:
        sys.stdout.write(rendered)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
