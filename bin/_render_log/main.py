"""CLI entry for `bin/render_log`.

Per implplan §C.impl.10. Produces `_orchestrator_log.md` from
`_orchestrator_log.jsonl`. Exit codes per the closed enum at the end of
§C.impl.10:

    0  render success
    1  missing/empty JSONL (warning, empty MD)
    2  corrupt JSONL detected (MD written up to + including first
       corrupt marker)
    3  output write failed
    4  argparse failure

CLI surface (per §C.impl.10 lines 1942-1948):

    bin/render_log <slug>
    bin/render_log --all
    bin/render_log --output <path> <slug>
    bin/render_log --since <ISO-ts> <slug>
    bin/render_log --llm-consumable <slug>

`--since` filters rows by `ts >= <ISO-ts>`; the comparison is string-wise
on the canonical ISO-8601-Z form, which is correct because the format is
lexicographically sortable.
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import sys
import tempfile
from typing import Sequence

from .md_emit import iter_md_lines


EXIT_OK = 0
EXIT_MISSING_JSONL = 1
EXIT_CORRUPT_JSONL = 2
EXIT_WRITE_FAILED = 3
EXIT_USAGE = 4


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DOCS_PLANS = REPO_ROOT / "docs" / "plans"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bin/render_log",
        description="Render docs/plans/<slug>/_orchestrator_log.jsonl to .md form.",
    )
    p.add_argument("slug", nargs="?", help="Plan slug (i.e., dir under docs/plans/).")
    p.add_argument("--all", action="store_true", help="Render every plan dir.")
    p.add_argument(
        "--output",
        type=pathlib.Path,
        default=None,
        help="Override output path (only valid with a single slug).",
    )
    p.add_argument(
        "--since",
        type=str,
        default=None,
        help="ISO-8601-Z timestamp; only render rows with ts >= this value.",
    )
    p.add_argument(
        "--llm-consumable",
        action="store_true",
        help="Emit <external-content>-wrapped form below each MD line.",
    )
    return p


def _header_lines(jsonl_path: pathlib.Path, rendered_at: str) -> list[str]:
    if jsonl_path.is_absolute():
        try:
            rel_jsonl = jsonl_path.relative_to(REPO_ROOT)
        except ValueError:
            rel_jsonl = jsonl_path
    else:
        rel_jsonl = jsonl_path
    return [
        "# Orchestrator log (rendered)",
        "",
        "Schema: 1",
        f"Source: {rel_jsonl}",
        f"Rendered at: {rendered_at}",
        "Render tool: bin/render_log",
        "",
    ]


def _atomic_write(target: pathlib.Path, body: str) -> None:
    """Write-temp + rename per cross-cutting "Atomic write discipline"."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=target.parent,
        prefix=".__render_log__",
        suffix=".md.tmp",
        delete=False,
    )
    try:
        tmp.write(body)
        tmp.flush()
        # fsync via the underlying fd for durability.
        import os

        os.fsync(tmp.fileno())
        tmp.close()
        pathlib.Path(tmp.name).replace(target)
    except Exception:
        tmp.close()
        try:
            pathlib.Path(tmp.name).unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _render_one(
    slug_dir: pathlib.Path,
    *,
    output: pathlib.Path | None,
    since: str | None,
    llm_consumable: bool,
) -> int:
    """Render a single plan dir's JSONL → MD. Returns exit code."""
    jsonl = slug_dir / "_orchestrator_log.jsonl"
    out_path = output or (slug_dir / "_orchestrator_log.md")

    rendered_at = (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    header = _header_lines(jsonl, rendered_at)

    if not jsonl.exists() or jsonl.stat().st_size == 0:
        # Per exit-code enum: 1 = missing/empty JSONL with warning.
        body = "\n".join(header + ["(no transitions recorded yet)"]) + "\n"
        try:
            _atomic_write(out_path, body)
        except OSError:
            return EXIT_WRITE_FAILED
        return EXIT_MISSING_JSONL

    lines: list[str] = list(header)
    saw_corrupt = False
    for md_line in iter_md_lines(jsonl, llm_consumable=llm_consumable):
        if since is not None and md_line.split(" | ", 1)[0] < since:
            # Skip rows whose ts < since. We do this on the rendered
            # line because the lexicographic compare on the ts prefix
            # is correct for ISO-8601-Z.
            # _corrupt lines never have a leading ISO ts and so never
            # filter out — that matches operator expectation (corrupt
            # markers are always visible).
            if md_line.startswith("_corrupt"):
                saw_corrupt = True
                lines.append(md_line)
            continue
        if md_line.startswith("_corrupt"):
            saw_corrupt = True
        lines.append(md_line)
    body = "\n".join(lines) + "\n"
    try:
        _atomic_write(out_path, body)
    except OSError:
        return EXIT_WRITE_FAILED
    return EXIT_CORRUPT_JSONL if saw_corrupt else EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse's default behavior is exit(2); remap to our enum.
        return EXIT_USAGE if exc.code != 0 else EXIT_OK

    if args.all and args.slug:
        print("error: --all is mutually exclusive with a positional slug", file=sys.stderr)
        return EXIT_USAGE
    if args.all and args.output:
        print("error: --output is only valid with a single slug", file=sys.stderr)
        return EXIT_USAGE
    if not args.all and not args.slug:
        parser.print_usage(sys.stderr)
        return EXIT_USAGE

    if args.all:
        worst = EXIT_OK
        if not DOCS_PLANS.exists():
            return EXIT_OK
        for slug_dir in sorted(DOCS_PLANS.iterdir()):
            if not slug_dir.is_dir():
                continue
            jsonl = slug_dir / "_orchestrator_log.jsonl"
            if not jsonl.exists():
                continue  # not a splock plan dir
            code = _render_one(
                slug_dir,
                output=None,
                since=args.since,
                llm_consumable=args.llm_consumable,
            )
            # Worst-of-N exit-code aggregation: higher code wins (3 > 2 > 1 > 0).
            if code > worst:
                worst = code
        return worst

    slug_dir = DOCS_PLANS / args.slug
    return _render_one(
        slug_dir,
        output=args.output,
        since=args.since,
        llm_consumable=args.llm_consumable,
    )


if __name__ == "__main__":
    sys.exit(main())
