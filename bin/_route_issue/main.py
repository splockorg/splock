"""CLI entry point for `bin/route_issue` (implplan §L.impl).

Dispatch order (per L.impl.4 + L.impl.5):

  1. argparse parse → Namespace
  2. If --check-scope: invoke triggers.evaluate; exit 25 if forced, else 0
  3. Otherwise: triggers.evaluate(...) FIRST
       forced=True → exit 25 (escalation trigger fired)
  4. Then rubric.route_after_triggers(category) → handler dispatch
  5. Handler return code is propagated verbatim

`--type escalate` bypasses rubric; main.py routes directly to escalate.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional

from . import cli, rubric, triggers
from .exit_codes import (
    EXIT_ESCALATION_TRIGGER_FIRED,
    EXIT_OK,
    EXIT_RUBRIC_REFUSE_NO_CATEGORY_FITS,
    EXIT_USAGE,
)


def _repo_root() -> Path:
    # bin/_route_issue/main.py → bin/_route_issue → bin → REPO_ROOT
    return Path(__file__).resolve().parents[2]


def dispatch(args, *, repo_root: Optional[Path] = None) -> int:
    repo_root = repo_root or _repo_root()

    # --- --check-scope meta path ---------------------------------------------
    if args.check_scope:
        ctx = triggers.RouteContext(
            repo_root=repo_root,
            include_ddl_count=args.include_ddl_count,
            check_override_state=args.check_override_state,
            allow_multi_ddl=args.allow_multi_ddl,
            slug=args.slug,
        )
        result = triggers.evaluate(ctx)
        if result.forced:
            _emit_trigger_refusal(result, args.json_output)
            return EXIT_ESCALATION_TRIGGER_FIRED
        if args.json_output:
            print(json.dumps({"result": "clean", "trigger": "none"}))
        else:
            print("scope check: clean")
        return EXIT_OK

    # --- --type required for non-meta path -----------------------------------
    if args.type_ is None:
        _emit_usage(
            "missing --type; choose one of: " + ", ".join(cli.VALID_TYPES) +
            "  (or use --check-scope)",
            args.json_output,
        )
        return EXIT_USAGE

    # --- Triggers fire BEFORE rubric (L.impl.4 + L.impl.5 critical ordering) -
    # Exception: --type escalate is the operator-direct shape that bypasses
    # triggers — operator is explicitly invoking the escalation surface.
    if args.type_ != "escalate":
        ctx = triggers.RouteContext(
            repo_root=repo_root,
            slug=args.slug,
        )
        result = triggers.evaluate(ctx)
        if result.forced:
            _emit_trigger_refusal(result, args.json_output)
            return EXIT_ESCALATION_TRIGGER_FIRED

    # --- Direct dispatch for escalate ----------------------------------------
    if args.type_ == "escalate":
        if not args.reason:
            _emit_usage("--type escalate requires --reason", args.json_output)
            return EXIT_USAGE
        if not args.context:
            _emit_usage("--type escalate requires --context", args.json_output)
            return EXIT_USAGE
        from . import escalate
        return escalate.run(
            reason=args.reason,
            context=args.context,
            trigger_source=args.trigger_source or "operator_direct",
            dry_run=args.dry_run,
            json_output=args.json_output,
            repo_root=repo_root,
            plan_slug=args.slug,
        )

    # --- Rubric routing for the four codified categories ---------------------
    decision = rubric.route_after_triggers(args.type_)
    if decision.refused:
        if args.json_output:
            print(json.dumps({
                "error": "rubric_refuse_no_category_fits",
                "category": decision.category,
                "detail": decision.detail,
            }))
        else:
            print(decision.detail, file=sys.stderr)
        return EXIT_RUBRIC_REFUSE_NO_CATEGORY_FITS

    # Handler dispatch
    if decision.handler == "fix_now":
        if not args.description:
            _emit_usage("--type fix-now requires --description", args.json_output)
            return EXIT_USAGE
        if not args.context:
            _emit_usage("--type fix-now requires --context", args.json_output)
            return EXIT_USAGE
        from . import fix_now
        return fix_now.run(
            description=args.description,
            context=args.context,
            dry_run=args.dry_run,
            json_output=args.json_output,
            repo_root=repo_root,
            plan_slug=args.slug,
        )

    if decision.handler == "outstanding":
        if not args.description:
            _emit_usage("--type outstanding requires --description", args.json_output)
            return EXIT_USAGE
        if not args.context:
            _emit_usage("--type outstanding requires --context", args.json_output)
            return EXIT_USAGE
        from . import outstanding
        return outstanding.run(
            description=args.description,
            context=args.context,
            blast_radius=args.blast_radius,
            related=args.related,
            dry_run=args.dry_run,
            json_output=args.json_output,
            repo_root=repo_root,
            plan_slug=args.slug,
        )

    if decision.handler == "marker_route":
        if not args.prefix:
            _emit_usage("--type marker requires --prefix", args.json_output)
            return EXIT_USAGE
        if not args.trigger:
            _emit_usage("--type marker requires --trigger", args.json_output)
            return EXIT_USAGE
        if not args.context:
            _emit_usage("--type marker requires --context", args.json_output)
            return EXIT_USAGE
        from . import marker_route
        return marker_route.run(
            prefix=args.prefix,
            trigger=args.trigger,
            context=args.context,
            title=args.marker_title,
            plan=args.plan,
            module=args.module,
            data_needed=args.data_needed,
            detail=args.detail,
            allow_na=args.allow_na,
            dry_run=args.dry_run,
            json_output=args.json_output,
            repo_root=repo_root,
            plan_slug=args.slug,
        )

    if decision.handler == "tier_promote":
        if not args.slug:
            _emit_usage("--type tier-promote requires --slug", args.json_output)
            return EXIT_USAGE
        if not args.context:
            _emit_usage(
                "--type tier-promote requires --context (origin line_id)",
                args.json_output,
            )
            return EXIT_USAGE
        from . import tier_promote
        return tier_promote.run(
            slug=args.slug,
            line_id=args.context,
            dry_run=args.dry_run,
            json_output=args.json_output,
            repo_root=repo_root,
        )

    _emit_usage(f"unknown handler dispatch: {decision.handler}", args.json_output)
    return EXIT_USAGE


def _emit_trigger_refusal(result: triggers.TriggerResult, json_output: bool) -> None:
    if json_output:
        print(json.dumps({
            "error": "escalation_trigger_fired",
            "trigger": result.trigger,
            "detail": result.detail,
            "staged_file_count": len(result.staged_files),
        }))
    else:
        print(
            f"REFUSED [escalation_trigger_fired/{result.trigger}]: {result.detail}",
            file=sys.stderr,
        )
        print(
            "  See plan §L.3 + research_findings_v1.md §D HiL-Bench mitigation. "
            "Use --type escalate to route to morning-review.",
            file=sys.stderr,
        )


def _emit_usage(msg: str, json_output: bool) -> None:
    if json_output:
        print(json.dumps({"error": "usage", "message": msg}))
    else:
        print(f"usage: {msg}", file=sys.stderr)


def main(argv: Optional[List[str]] = None) -> int:
    args = cli.parse_args(argv)
    try:
        return dispatch(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
