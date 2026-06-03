"""CLI entry point for `bin/eval-trend` (§J.impl.10).

Operator-as-terminator anchor: this CLI surfaces threshold breaches as
morning-review rows for OPERATOR review. There is no meta-scorer
above the operator's ground-truth labels.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from dataclasses import asdict
from typing import Optional

from bin._eval_common.scorer_registry import SCORER_IDS, SCORER_KIND, InvalidEnumError

from . import calibration as calibration_module
from . import recent as recent_module
from . import thresholds
from .exit_codes import EXIT_OK, EXIT_UNSUPPORTED_SCHEMA, EXIT_USAGE


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def _session_id() -> str:
    import hashlib

    sid = os.environ.get("CLAUDE_SESSION_ID")
    if sid:
        return sid
    digest = hashlib.sha1(f"eval-trend|{os.getpid()}".encode("utf-8")).hexdigest()
    return f"sess_{digest[:8]}"


def _emit_breach_row(
    plan_dir: pathlib.Path,
    *,
    slug: str,
    breach: thresholds.BreachResult,
) -> None:
    """Emit `scorer_calibration_threshold_breach` row → morning-review queue."""
    try:
        from bin._jsonl_log.writer import append_row
    except ImportError:
        return
    rule = breach.rule
    row = {
        "session_id": _session_id(),
        "plan_slug": slug,
        "task_id": None,
        "transition": {"from": "wip", "to": "deferred"},
        "mode_at_transition": {
            "overnight": os.environ.get("OVERNIGHT_MODE") == "1",
            "guardrail": False,
        },
        "reason": (
            f"scorer calibration threshold breach on {rule.scorer_id}: "
            f"{rule.metric}={breach.observed_value:.4f}"
        ),
        "event_type": "scorer_calibration_threshold_breach",
        "attributes": {
            "scorer_id": rule.scorer_id,
            "metric": rule.metric,
            "threshold": rule.threshold,
            "observed_value": breach.observed_value,
            "n_labels": breach.n_labels,
        },
    }
    try:
        append_row(plan_dir, row, "bin/eval-trend")
    except Exception:
        pass


def _stats_payload(plan_dir: pathlib.Path, scorer_id: str) -> dict:
    kind = SCORER_KIND.get(scorer_id)
    if kind == "binary":
        s = calibration_module.binary_stats_for(plan_dir, scorer_id)
        return asdict(s) if s else {}
    if kind == "ordinal":
        s = calibration_module.ordinal_stats_for(plan_dir, scorer_id)
        return asdict(s) if s else {}
    if kind == "numeric":
        s = calibration_module.numeric_trend_for(plan_dir, scorer_id)
        return asdict(s) if s else {}
    return {}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bin/eval-trend",
        description="Surface calibration metrics + threshold breaches for operator review.",
    )
    p.add_argument("slug")
    p.add_argument("--scorer", default=None, help="Filter to one scorer_id")
    p.add_argument(
        "--window",
        type=int,
        default=None,
        help="Last N emissions (unbounded if omitted)",
    )
    p.add_argument(
        "--vs-ground-truth",
        action="store_true",
        dest="vs_ground_truth",
        help="Compute calibration metrics over labeled subset",
    )
    p.add_argument(
        "--recent",
        type=int,
        default=None,
        help="Surface free-text scorer history",
    )
    p.add_argument("--json", action="store_true", dest="json_output")
    p.add_argument(
        "--emit-breach-rows",
        action="store_true",
        dest="emit_breach_rows",
        help="Emit morning-review rows on threshold breach (default: on)",
        default=True,
    )
    p.add_argument(
        "--no-emit-breach-rows",
        action="store_false",
        dest="emit_breach_rows",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    plan_dir = _repo_root() / "docs" / "plans" / args.slug
    if not plan_dir.exists():
        print(f"plan_dir does not exist: {plan_dir}", file=sys.stderr)
        return EXIT_USAGE

    scorer_ids = [args.scorer] if args.scorer else sorted(SCORER_IDS)

    payload: dict = {"slug": args.slug, "scorers": {}}
    try:
        for sid in scorer_ids:
            if sid not in SCORER_IDS:
                print(f"unknown scorer_id: {sid!r}", file=sys.stderr)
                return EXIT_USAGE
            spayload: dict = {"kind": SCORER_KIND.get(sid)}

            if args.recent is not None and SCORER_KIND.get(sid) == "free_text":
                spayload["recent"] = recent_module.recent_for_scorer(
                    plan_dir, sid, args.recent
                )
            else:
                spayload["stats"] = _stats_payload(plan_dir, sid)

            # Threshold checks.
            for rule in thresholds.rules_for_scorer(sid):
                stats = spayload["stats"]
                observed = stats.get(rule.metric) if isinstance(stats, dict) else None
                n_labels = stats.get("n_labels", 0) if isinstance(stats, dict) else 0
                if observed is None:
                    continue
                result = thresholds.check(
                    rule,
                    observed_value=float(observed),
                    n_labels=int(n_labels),
                )
                if isinstance(result, thresholds.BreachResult):
                    spayload.setdefault("breaches", []).append(
                        {
                            "metric": rule.metric,
                            "threshold": rule.threshold,
                            "observed_value": result.observed_value,
                            "n_labels": result.n_labels,
                        }
                    )
                    if args.emit_breach_rows:
                        _emit_breach_row(
                            plan_dir, slug=args.slug, breach=result
                        )

            payload["scorers"][sid] = spayload
    except InvalidEnumError as exc:
        # Forward-compat refusal: unknown schema_version in shipped JSONL.
        # Per §J.impl.3 + §J.impl.4 + §B.impl.6 exit-5 contract.
        print(
            json.dumps(
                {
                    "error": "unsupported_schema_version",
                    "detail": str(exc),
                }
            ),
            file=sys.stderr,
        )
        return EXIT_UNSUPPORTED_SCHEMA

    if args.json_output:
        print(json.dumps(payload, default=str))
    else:
        for sid, sp in payload["scorers"].items():
            print(f"## {sid} ({sp.get('kind')})")
            if "stats" in sp:
                print(f"  stats: {sp['stats']}")
            if "breaches" in sp:
                for b in sp["breaches"]:
                    print(f"  BREACH {b}")
            if "recent" in sp:
                for r in sp["recent"]:
                    print(f"  {r.get('ts')} {r.get('score_value')}")

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
