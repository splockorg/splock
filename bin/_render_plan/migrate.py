"""Operator-discretion migration helper for legacy MD-only plans.

Per implplan §B.impl.8 lines 1317-1326: this is a guided template, not
a deterministic transformer. Every field requires operator confirmation.

The implementation deliberately keeps the UX minimal — the policy is
net-new plans only, so `bin/migrate_plan` is rarely invoked. Phase 1
ships a working scaffold; richer prompting can be added if field signal
indicates operators want it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import exit_codes
from .atomic_write import AtomicWriteError, write_atomic
from .json_loader import load_plan_json, validate_against_schema, SchemaRejectedError

from bin._env_paths import plans_dir as _env_paths_plans_dir

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLANS_DIR = _env_paths_plans_dir()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bin/migrate_plan",
        description=(
            "Operator-guided helper for onboarding a legacy MD-only plan "
            "to the JSON-canonical substrate. Net-new plans only is the "
            "policy; this CLI exists for the rare onboarding case."
        ),
    )
    parser.add_argument("slug", help="kebab-case plan slug")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "Skip prompts (assumes stdin will not be available). "
            "Writes a stub <slug>_plan.json that the operator hand-edits "
            "to compliance."
        ),
    )
    args = parser.parse_args(argv)

    plan_dir = _PLANS_DIR / args.slug
    md_path = plan_dir / f"{args.slug}_plan.md"
    json_path = plan_dir / f"{args.slug}_plan.json"

    if not plan_dir.exists():
        print(
            f"migrate_plan: plan dir not found: {plan_dir}", file=sys.stderr
        )
        return exit_codes.EXIT_PLAN_NOT_FOUND
    if json_path.exists():
        print(
            f"migrate_plan: refusing — {json_path} already exists; "
            "use bin/render_plan instead.",
            file=sys.stderr,
        )
        return exit_codes.EXIT_USAGE
    if not md_path.exists():
        print(
            f"migrate_plan: no legacy MD found at {md_path}; "
            "create one or use /plan to author a new plan.",
            file=sys.stderr,
        )
        return exit_codes.EXIT_PLAN_NOT_FOUND

    # Read legacy MD; surface the first heading as title hint.
    md_text = md_path.read_text(encoding="utf-8")
    title_hint = _first_h1(md_text) or args.slug

    print(
        f"migrate_plan: drafting {json_path} from {md_path}", file=sys.stderr
    )
    print(
        "  Note: this is a STUB. Hand-edit the JSON to compliance, then "
        "run `bin/verify_plan <slug>`.",
        file=sys.stderr,
    )

    stub: dict = {
        "schema_version": 1,
        "slug": args.slug,
        "phase": "Phase 2",
        "title": title_hint,
        "problem_statement": "",
        "success_criteria": [],
        "tasks_skeleton": [],
        "tier": "Tier 2",
    }

    try:
        write_atomic(json_path, json.dumps(stub, indent=2) + "\n")
    except AtomicWriteError as exc:
        print(
            f"migrate_plan: atomic write failed: {exc}", file=sys.stderr
        )
        return exit_codes.EXIT_ATOMIC_WRITE_FAILED

    # Quick verify pass; the stub will fail because TODOs are not real
    # criteria — operator's job to hand-fill. Report status either way.
    try:
        loaded = load_plan_json(json_path)
        validate_against_schema(loaded, "plan", source_path=str(json_path))
        print(
            "migrate_plan: stub passed schema validation (unlikely; "
            "double-check that all TODOs are filled).",
            file=sys.stderr,
        )
    except SchemaRejectedError as exc:
        # Expected outcome — stub is incomplete by design.
        print(
            "migrate_plan: stub validation reports expected gaps; "
            "hand-fill and re-verify with bin/verify_plan.",
            file=sys.stderr,
        )

    return exit_codes.EXIT_OK


def _first_h1(md_text: str) -> str | None:
    for line in md_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return None


if __name__ == "__main__":
    sys.exit(main())
