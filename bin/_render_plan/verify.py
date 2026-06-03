"""Schema validation entry for `bin/verify_plan`.

Per implplan §B.impl.4 + §B.impl.7 + §B.impl.8: this is the validator
that the chain driver invokes with `--strict` after Call 2 writes the
JSON, before `bin/render_plan` regenerates the MD.

The lax (default) mode is the fast "does this parse?" feedback path for
ad-hoc operator usage. Strict mode adds the §B.impl.9 invariants:
- Task-id uniqueness within a document
- `depends_on` references resolve to defined task IDs
- `plan_ref` in orchestrator resolves to an existing `<slug>_plan.json`
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import exit_codes
from .json_loader import (
    JsonMalformedError,
    PlanNotFoundError,
    SchemaRejectedError,
    UnsupportedSchemaVersion,
    load_plan_json,
    validate_against_schema,
)

PlanKind = Literal["plan", "orchestrator"]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLANS_DIR = _REPO_ROOT / "docs" / "plans"


@dataclass
class VerifyPaths:
    json_path: Path
    slug: str
    kind: PlanKind


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exit_codes.EXIT_USAGE if exc.code != 0 else exit_codes.EXIT_OK

    try:
        paths = _resolve_paths(args)
    except _UsageError as exc:
        print(f"verify_plan: {exc}", file=sys.stderr)
        return exit_codes.EXIT_USAGE

    if not paths.json_path.exists():
        # Per implplan §B.impl.8 line 1311-1313: legacy MD-only plans
        # refused with structured stderr + EXIT_PLAN_NOT_FOUND.
        md_sibling = paths.json_path.with_suffix(".md")
        legacy = md_sibling.exists()
        payload = {"error": "plan_json_missing", "slug": paths.slug}
        if legacy:
            payload["hint"] = (
                "net-new plans only per plan §B.8; use bin/migrate_plan "
                "for operator-driven onboarding"
            )
        _emit_stderr_json(payload)
        return exit_codes.EXIT_PLAN_NOT_FOUND

    try:
        plan = load_plan_json(paths.json_path)
    except PlanNotFoundError:
        _emit_stderr_json(
            {"error": "plan_not_found", "path": str(paths.json_path)}
        )
        return exit_codes.EXIT_PLAN_NOT_FOUND
    except JsonMalformedError as exc:
        _emit_stderr_json(exc.as_stderr_payload())
        return exit_codes.EXIT_JSON_MALFORMED

    try:
        validate_against_schema(
            plan, paths.kind, source_path=str(paths.json_path)
        )
    except UnsupportedSchemaVersion as exc:
        _emit_stderr_json(exc.as_stderr_payload())
        return exit_codes.EXIT_UNSUPPORTED_SCHEMA_VERSION
    except SchemaRejectedError as exc:
        _emit_stderr_json(exc.as_stderr_payload())
        return exit_codes.EXIT_SCHEMA_REJECTED

    if args.strict:
        # Import here to avoid circular-import risk.
        from bin._verify_plan.strict import run_strict_invariants

        try:
            run_strict_invariants(plan, paths.kind, paths.json_path)
        except SchemaRejectedError as exc:
            _emit_stderr_json(exc.as_stderr_payload())
            return exit_codes.EXIT_SCHEMA_REJECTED

    return exit_codes.EXIT_OK


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class _UsageError(ValueError):
    """Argument resolution failed; map to EXIT_USAGE."""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin/verify_plan",
        description=(
            "Validate <slug>_plan.json (or <slug>_orchestrator.json) "
            "against the canonical schema. With --strict, additional "
            "invariants (task-id uniqueness, depends_on resolution, "
            "plan_ref existence) are enforced."
        ),
    )
    parser.add_argument(
        "target",
        help=(
            "Either a kebab-case slug (resolves to docs/plans/<slug>/) "
            "or a direct path to a *_plan.json / *_orchestrator.json file."
        ),
    )
    parser.add_argument(
        "--kind",
        choices=("plan", "orchestrator"),
        default="plan",
        help="Substrate kind. Defaults to `plan`.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Add cross-field invariants beyond the JSON Schema. Chain "
            "driver always invokes with --strict."
        ),
    )
    return parser


def _resolve_paths(args: argparse.Namespace) -> VerifyPaths:
    kind: PlanKind = args.kind
    target = Path(args.target)
    # Direct-path mode if it looks like a path (has separator or .json suffix).
    if target.suffix == ".json" or "/" in args.target:
        json_path = target.resolve()
        stem = json_path.stem
        slug = stem
        for suffix in ("_plan", "_orchestrator"):
            if stem.endswith(suffix):
                slug = stem[: -len(suffix)]
                # Auto-detect kind from filename if user didn't override.
                if suffix == "_orchestrator" and kind == "plan":
                    kind = "orchestrator"
                break
        return VerifyPaths(json_path=json_path, slug=slug, kind=kind)
    # Slug mode.
    slug = args.target
    plan_dir = _PLANS_DIR / slug
    suffix = "_plan" if kind == "plan" else "_orchestrator"
    json_path = plan_dir / f"{slug}{suffix}.json"
    return VerifyPaths(json_path=json_path, slug=slug, kind=kind)


def _emit_stderr_json(payload: dict) -> None:
    sys.stderr.write(json.dumps(payload, sort_keys=False) + "\n")


if __name__ == "__main__":
    sys.exit(main())
