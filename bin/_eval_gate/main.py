"""CLI entry point for `bin/eval-gate` (§J.impl.9)."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
from typing import Optional

from bin._eval_baseline.manifest import baseline_dir, latest_baseline
from bin._eval_baseline.mint import read_case_scores

from . import compare as compare_module
from . import dispatch as dispatch_module
from . import replay
from . import touch_paths
from .exit_codes import (
    EXIT_BASELINE_MISSING,
    EXIT_DATASET_EMPTY,
    EXIT_EVAL_GATE_REGRESSION,
    EXIT_OK,
    EXIT_USAGE,
)


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def _staged_files(repo_root: pathlib.Path) -> list[str]:
    """`git diff --cached --name-only` — returns [] on git failure."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_root), "diff", "--cached", "--name-only"],
            stderr=subprocess.DEVNULL,
        )
        return [ln for ln in out.decode("utf-8").splitlines() if ln.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return []


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bin/eval-gate",
        description="Replay regression cases against baseline; gate commits.",
    )
    p.add_argument("--slug", default=None, help="Override $SPLOCK_PLAN_SLUG")
    p.add_argument("--vs-baseline", dest="vs_baseline", default=None)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--report-only", action="store_true", dest="report_only")
    p.add_argument("--case-ids", default=None, dest="case_ids")
    p.add_argument("--json", action="store_true", dest="json_output")
    p.add_argument(
        "--from-precommit-hook",
        action="store_true",
        dest="from_precommit",
    )
    p.add_argument(
        "--internal-mint-mode", action="store_true", dest="internal_mint_mode"
    )
    p.add_argument(
        "--staged-files",
        default=None,
        help="Comma-separated override of staged files (for testing).",
    )
    return p


def _session_id_for_eval_gate() -> str:
    """Derive a deterministic session_id matching the schema pattern
    `^sess_[0-9a-f]{8}$` per orchestrator_log_v1."""
    import hashlib

    sid = os.environ.get("CLAUDE_SESSION_ID")
    if sid:
        return sid
    digest = hashlib.sha1(f"eval-gate|{os.getpid()}".encode("utf-8")).hexdigest()
    return f"sess_{digest[:8]}"


def _emit_chain_regression_row(
    plan_dir: pathlib.Path,
    *,
    plan_slug: str,
    chain_id: Optional[str],
    regressions: list[str],
) -> None:
    """Emit `event_type: eval_gate_regression_detected` via §C shared writer."""
    try:
        from bin._jsonl_log.writer import append_row
    except ImportError:
        return
    row = {
        "session_id": _session_id_for_eval_gate(),
        "plan_slug": plan_slug,
        "task_id": None,
        "transition": {"from": "wip", "to": "deferred"},
        "mode_at_transition": {
            "overnight": os.environ.get("OVERNIGHT_MODE") == "1",
            "guardrail": False,
        },
        "reason": f"eval-gate regression on case_ids {','.join(regressions)}",
        "event_type": "eval_gate_regression_detected",
    }
    if chain_id:
        row["chain_id"] = chain_id
    try:
        append_row(plan_dir, row, "bin/eval-gate")
    except Exception:
        # Best-effort: chain-driver branch should never hard-fail.
        pass


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = _repo_root()

    # Slug resolution: --slug overrides; else $SPLOCK_PLAN_SLUG.
    slug = args.slug or os.environ.get("SPLOCK_PLAN_SLUG")

    # Touch-path check.
    if args.staged_files is not None:
        staged = [s for s in args.staged_files.split(",") if s]
    else:
        staged = _staged_files(repo_root)
    matched = touch_paths.is_system_touch(staged)
    dispatch = dispatch_module.resolve(matched)

    if dispatch.branch == "no_touch_path":
        if args.json_output:
            print(json.dumps({"branch": "no_touch_path"}))
        return EXIT_OK

    # Need a plan_dir to proceed. Without slug, we cannot compare.
    if not slug:
        if args.json_output:
            print(json.dumps({"branch": dispatch.branch, "warn": "no_slug"}))
        else:
            print("eval-gate: no slug — skip", file=sys.stderr)
        return EXIT_OK
    plan_dir = repo_root / "docs" / "plans" / slug
    if not plan_dir.exists():
        if args.json_output:
            print(json.dumps({"branch": dispatch.branch, "warn": "no_plan_dir"}))
        return EXIT_OK

    # Baseline resolution.
    baseline_name = args.vs_baseline or latest_baseline(plan_dir)
    if not baseline_name:
        # First commit before mint is legitimate per §J.impl.9.
        if args.json_output:
            print(
                json.dumps(
                    {"branch": dispatch.branch, "warn": "baseline_missing"}
                )
            )
        else:
            print(
                "eval-gate: no baseline minted yet (warn-not-fail)",
                file=sys.stderr,
            )
        return EXIT_BASELINE_MISSING if args.strict else EXIT_OK

    bdir = baseline_dir(plan_dir, baseline_name)
    if not bdir.exists():
        if args.json_output:
            print(
                json.dumps(
                    {
                        "branch": dispatch.branch,
                        "error": "baseline_missing",
                        "name": baseline_name,
                    }
                )
            )
        else:
            print(f"baseline not found: {baseline_name}", file=sys.stderr)
        return EXIT_BASELINE_MISSING

    # Run comparison.
    baseline_rows = read_case_scores(plan_dir, baseline_name)
    active = replay.collect_active_cases(plan_dir)
    if not active:
        return EXIT_DATASET_EMPTY
    result = compare_module.compare(
        baseline_case_rows=baseline_rows, active_cases=active
    )

    payload = {
        "branch": dispatch.branch,
        "baseline": baseline_name,
        "regressions": list(result.regressions),
        "missing": list(result.missing),
        "extra": list(result.extra),
        "matched": result.matched,
    }

    if args.json_output:
        print(json.dumps(payload))
    else:
        print(
            f"eval-gate branch={dispatch.branch} baseline={baseline_name} "
            f"matched={result.matched} regressions={len(result.regressions)} "
            f"missing={len(result.missing)} extra={len(result.extra)}"
        )

    # Verdict by branch.
    if not result.regressions:
        return EXIT_OK

    if dispatch.branch == "interactive_strict":
        # Escape-hatch: EVAL_GATE_OVERRIDE permits commit + loud-log.
        if os.environ.get("EVAL_GATE_OVERRIDE") == "1":
            reason = os.environ.get(
                "EVAL_GATE_OVERRIDE_REASON", "override unspecified"
            )
            try:
                from bin._jsonl_log.writer import append_row

                ov_row = {
                    "session_id": _session_id_for_eval_gate(),
                    "plan_slug": slug,
                    "task_id": None,
                    "transition": {"from": "wip", "to": "wip"},
                    "mode_at_transition": {
                        "overnight": False,
                        "guardrail": False,
                    },
                    "override_in_effect": {
                        "operator_override": True,
                        "operator_override_state": None,
                        "driver_override": None,
                    },
                    "reason": f"eval_gate_override_applied: {reason}",
                    "event_type": "eval_gate_regression_detected",
                }
                append_row(plan_dir, ov_row, "bin/eval-gate")
            except Exception:
                pass
            return EXIT_OK
        return EXIT_EVAL_GATE_REGRESSION

    # chain_report_only — always permit; emit needs-review row.
    _emit_chain_regression_row(
        plan_dir,
        plan_slug=slug,
        chain_id=dispatch.chain_id,
        regressions=list(result.regressions),
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
