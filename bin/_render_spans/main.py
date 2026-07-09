"""CLI entry point for `bin/render_spans` (§J.impl.3)."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Optional

from bin._render_plan.atomic_write import AtomicWriteError, write_atomic

from . import derive as derive_module
from .exit_codes import (
    EXIT_ATOMIC_WRITE_FAILED,
    EXIT_OK,
    EXIT_UNSUPPORTED_SCHEMA,
    EXIT_USAGE,
)


def _plans_dir() -> pathlib.Path:
    """The ADOPTER's plan dir — its logs are the input, its `_spans.jsonl` the output.

    Upstream walked `parents[2] / "docs" / "plans"`, which under an installed
    plugin is the plugin cache: the CLI would derive spans from the plugin's own
    (empty) plan tree and write `_spans.jsonl` into it.
    """
    from bin._env_paths import plans_dir

    return plans_dir()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bin/render_spans",
        description="Derive OpenInference-shape spans for a plan slug.",
    )
    p.add_argument("slug")
    p.add_argument("--json", action="store_true", dest="json_output")
    p.add_argument(
        "--stdout",
        action="store_true",
        help="Print to stdout instead of writing _spans.jsonl",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    plan_dir = _plans_dir() / args.slug
    if not plan_dir.exists():
        sys.stderr.write(f"plan_dir does not exist: {plan_dir}\n")
        return EXIT_USAGE

    spans = derive_module.derive(plan_dir)
    rows = [s.to_dict() for s in spans]
    body = "".join(
        json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        for r in rows
    )
    if args.stdout:
        sys.stdout.write(body)
        return EXIT_OK
    target = plan_dir / "_spans.jsonl"
    try:
        write_atomic(target, body)
    except AtomicWriteError as exc:
        sys.stderr.write(f"atomic_write_failed: {exc}\n")
        return EXIT_ATOMIC_WRITE_FAILED
    if args.json_output:
        print(json.dumps({"slug": args.slug, "span_count": len(rows)}))
    else:
        print(f"wrote {len(rows)} spans → {target}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
