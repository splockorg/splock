"""CLI entry for `bin/state-divergence-check`.

Per implplan §C.impl.4 (Finding 18 / Hole H.13). Detects forensic
divergence between the canonical JSONL log and the on-disk projection
`_state.json`. On divergence the log wins (per source-of-truth
declaration in plan §C.2).

CLI surface (per implplan §C.impl.4 lines 1607-1612):

    bin/state-divergence-check <slug>
    bin/state-divergence-check --all
    bin/state-divergence-check --strict <slug>
    bin/state-divergence-check --json <slug>

Exit codes (closed enum):

    0  clean (no divergence detected)
    1  diverged — only under --strict (per implplan §C.impl.4 line 1644)
    2  log corrupt — replay aborted; manual intervention required
    3  input missing — slug dir or JSONL not found
    4  argparse failure

F-04 (pre-Phase 1 Sonnet review) per-slug nightly write path
------------------------------------------------------------
Per implplan §C.impl.4 lines 1648-1657 — the v1.5-audit-response
correction. Under `--all --strict --json` (nightly cron invocation), the
divergence report is appended as a per-slug section to
`docs/plans/<slug>/morning-review/<YYYY-MM-DD>.md` (NOT the retired flat
`docs/morning-review/<date>.md` path). One file per slug per day, with
sections coexisting via append-mode writes; section headers are
unambiguous so `bin/morning-review` (§H.impl) can identify and consume
the auto-generated audit blocks.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
from typing import Sequence

from .replay import check_one


EXIT_OK = 0
EXIT_DIVERGED = 1
EXIT_LOG_CORRUPT = 2
EXIT_INPUT_MISSING = 3
EXIT_USAGE = 4


# Plan logs and `_state.json` are ADOPTER data. Upstream anchors on
# `parent.parent.parent`, which under an installed plugin is the plugin cache:
# the replay would then find no plans and report "no divergence" vacuously.
from bin._env_paths import plans_dir as _env_paths_plans_dir

DOCS_PLANS = _env_paths_plans_dir()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bin/state-divergence-check",
        description=(
            "Detect divergence between `_orchestrator_log.jsonl` (canonical) "
            "and `_state.json` (projection). On divergence the log wins."
        ),
    )
    p.add_argument(
        "slug",
        nargs="?",
        help="Plan slug (dir under docs/plans/). Omit with --all.",
    )
    p.add_argument("--all", action="store_true", help="Iterate every plan dir.")
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on any divergence (default: report-only).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit the report as JSON to stdout (default: human-readable).",
    )
    return p


def _today_iso_date() -> str:
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def _morning_review_path(slug: str) -> pathlib.Path:
    """Per-slug morning-review file for today (F-04 path).

    `docs/plans/<slug>/morning-review/<YYYY-MM-DD>.md`. Created with
    parents on demand.
    """
    return (
        DOCS_PLANS
        / slug
        / "morning-review"
        / f"{_today_iso_date()}.md"
    )


def _write_morning_review_section(slug: str, report: dict) -> None:
    """Append the structured divergence report as a per-slug section.

    Section header is `## State-divergence audit (auto-generated <ISO-ts>)`
    so `bin/morning-review` (§H.impl) can identify auto-generated audit
    sections distinctly from operator-entered queue items. Append-mode so
    multiple sections coexist within the same daily file.
    """
    out = _morning_review_path(slug)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        out.touch()
    section_lines = [
        "",
        f"## State-divergence audit (auto-generated {report['checked_at']})",
        "",
        f"- result: `{report['result']}`",
        f"- log_path: `{report['log_path']}`",
        f"- state_path: `{report['state_path']}`",
        f"- log_replay_rows: {report['log_replay_rows']}",
    ]
    if report["divergences"]:
        section_lines.append("- divergences:")
        for d in report["divergences"]:
            section_lines.append(
                f"    - {d['task_id']}: log_says=`{d['log_says']}`, "
                f"state_says=`{d['state_says']}`, "
                f"ref=`{d['last_log_row_ref']}`, ts=`{d['last_log_row_ts']}`"
            )
    else:
        section_lines.append("- divergences: none")
    section_lines.append("")
    section_lines.append("```json")
    section_lines.append(json.dumps(report, indent=2, ensure_ascii=False))
    section_lines.append("```")
    section_lines.append("")
    with out.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(section_lines) + "\n")


def _emit_report(report: dict, *, json_mode: bool) -> None:
    if json_mode:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return
    # Human-readable.
    sys.stdout.write(f"slug:    {report['slug']}\n")
    sys.stdout.write(f"result:  {report['result']}\n")
    sys.stdout.write(f"log:     {report['log_path']}\n")
    sys.stdout.write(f"state:   {report['state_path']}\n")
    sys.stdout.write(f"rows:    {report['log_replay_rows']}\n")
    if report["divergences"]:
        sys.stdout.write("divergences:\n")
        for d in report["divergences"]:
            sys.stdout.write(
                f"  - {d['task_id']}: log_says={d['log_says']!r} "
                f"vs state_says={d['state_says']!r} "
                f"({d['last_log_row_ref']} @ {d['last_log_row_ts']})\n"
            )
    else:
        sys.stdout.write("divergences: none\n")


def _result_to_exit(report: dict, *, strict: bool) -> int:
    res = report["result"]
    if res == "clean":
        return EXIT_OK
    if res == "log_corrupt":
        return EXIT_LOG_CORRUPT
    if res == "diverged":
        return EXIT_DIVERGED if strict else EXIT_OK
    # Defensive fallback — should not happen.
    return EXIT_OK


def _check_slug(
    slug: str, *, strict: bool, json_mode: bool, nightly_write: bool
) -> int:
    slug_dir = DOCS_PLANS / slug
    if not slug_dir.exists() or not slug_dir.is_dir():
        print(
            f"error: plan dir not found: {slug_dir}",
            file=sys.stderr,
        )
        return EXIT_INPUT_MISSING
    jsonl = slug_dir / "_orchestrator_log.jsonl"
    if not jsonl.exists():
        print(
            f"error: _orchestrator_log.jsonl not found in {slug_dir}",
            file=sys.stderr,
        )
        return EXIT_INPUT_MISSING
    report = check_one(slug, slug_dir)
    if nightly_write:
        _write_morning_review_section(slug, report)
    _emit_report(report, json_mode=json_mode)
    return _result_to_exit(report, strict=strict)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code != 0 else EXIT_OK
    if args.all and args.slug:
        print("error: --all is mutually exclusive with a positional slug", file=sys.stderr)
        return EXIT_USAGE
    if not args.all and not args.slug:
        parser.print_usage(sys.stderr)
        return EXIT_USAGE
    # Nightly cron invocation: --all --strict --json. F-04 per-slug write
    # path engages when ALL three flags are present (matches the implplan
    # §C.impl.4 "scheduled task" pattern).
    nightly_write = bool(args.all and args.strict and args.json)
    if args.all:
        worst = EXIT_OK
        if not DOCS_PLANS.exists():
            return EXIT_OK
        any_processed = False
        for slug_dir in sorted(DOCS_PLANS.iterdir()):
            if not slug_dir.is_dir():
                continue
            jsonl = slug_dir / "_orchestrator_log.jsonl"
            if not jsonl.exists():
                continue
            any_processed = True
            slug = slug_dir.name
            code = _check_slug(
                slug,
                strict=args.strict,
                json_mode=args.json,
                nightly_write=nightly_write,
            )
            # Worst-of-N exit aggregation. log_corrupt (2) outranks
            # diverged (1) outranks ok (0). EXIT_INPUT_MISSING (3) is
            # not expected in --all because we filter on jsonl-existence
            # above, but we still let it bubble.
            if code > worst:
                worst = code
        return worst if any_processed else EXIT_OK

    return _check_slug(
        args.slug,
        strict=args.strict,
        json_mode=args.json,
        nightly_write=False,
    )


if __name__ == "__main__":
    sys.exit(main())
