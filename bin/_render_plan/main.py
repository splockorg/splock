"""CLI entry point for `bin/render_plan`.

Per implplan §B.impl.4 (lines 1103-1175). Implements the 10-step render
flow:

    1. Parse args; resolve <slug> to docs/plans/<slug>/ plan dir
    2. Read <slug>_plan.json (or --from-json <path> override)
    3. Dispatch to schema registry; reject unknown future schema_version
    4. Validate JSON against resolved schema
    5. Read existing <slug>_plan.md; extract anchor-section content
    6. Render canonical MD body from JSON via template
    7. Detect outside-anchor edits; emit warning if non-empty
    8. Re-insert preserved human-notes content
    9. Atomic write-temp + os.replace to MD path
    10. Emit render event row — Phase 1 NO-OP per OO-4 (see comment in `main()`)

`bin/verify_plan` (the sibling CLI) uses a different `main`; the
shared modules live here.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import exit_codes
from .atomic_write import AtomicWriteError, write_atomic
from .human_notes import (
    detect_outside_anchor_diff,
    extract_anchor_content,
)
from .json_loader import (
    JsonMalformedError,
    PlanNotFoundError,
    SchemaRejectedError,
    UnsupportedSchemaVersion,
    load_plan_json,
    validate_against_schema,
)
from .md_parser import read_existing_md
from .md_renderer import TemplateError, render_canonical_body

PlanKind = Literal["plan", "orchestrator", "state"]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLANS_DIR = _REPO_ROOT / "docs" / "plans"


@dataclass
class RenderPaths:
    """Resolved paths for a render invocation."""

    json_path: Path
    md_path: Path
    slug: str
    kind: PlanKind


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse already wrote to stderr; convert its exit code to ours.
        return exit_codes.EXIT_USAGE if exc.code != 0 else exit_codes.EXIT_OK

    try:
        paths = _resolve_paths(args)
    except _UsageError as exc:
        print(f"render_plan: {exc}", file=sys.stderr)
        return exit_codes.EXIT_USAGE

    # Step 2: load JSON.
    try:
        plan = load_plan_json(paths.json_path)
    except PlanNotFoundError as exc:
        _emit_stderr_json(
            {"error": "plan_not_found", "path": str(paths.json_path)}
        )
        return exit_codes.EXIT_PLAN_NOT_FOUND
    except JsonMalformedError as exc:
        _emit_stderr_json(exc.as_stderr_payload())
        return exit_codes.EXIT_JSON_MALFORMED

    # Steps 3-4: schema-version dispatch + content validation.
    try:
        validate_against_schema(plan, paths.kind, source_path=str(paths.json_path))
    except UnsupportedSchemaVersion as exc:
        _emit_stderr_json(exc.as_stderr_payload())
        return exit_codes.EXIT_UNSUPPORTED_SCHEMA_VERSION
    except SchemaRejectedError as exc:
        _emit_stderr_json(exc.as_stderr_payload())
        return exit_codes.EXIT_SCHEMA_REJECTED

    # Step 5: read existing MD + extract anchor content.
    existing_md = read_existing_md(paths.md_path)
    anchor_result = extract_anchor_content(existing_md)
    if anchor_result.warning:
        print(
            f"render_plan: WARNING {anchor_result.warning}",
            file=sys.stderr,
        )
    for extra_warning in anchor_result.warnings[1:]:
        print(
            f"render_plan: WARNING {extra_warning}",
            file=sys.stderr,
        )

    # Step 6: render canonical MD body.
    try:
        # Render once with anchor content; we'll also produce a "naked"
        # canonical body for the outside-anchor diff in step 7.
        canonical_body_naked = render_canonical_body(
            plan, paths.kind, human_notes_content=""
        )
        rendered = render_canonical_body(
            plan, paths.kind, human_notes_content=anchor_result.content
        )
    except TemplateError as exc:
        _emit_stderr_json({"error": "template_error", "message": str(exc)})
        return exit_codes.EXIT_TEMPLATE_ERROR

    # Step 7: detect outside-anchor edits (warning only, per OO-1 RATIFIED).
    if existing_md is not None:
        hunks = detect_outside_anchor_diff(existing_md, canonical_body_naked)
        if hunks:
            print(
                "render_plan: WARNING render-plan outside-anchor-edit-clobbered",
                file=sys.stderr,
            )

    # Step 8: anchor content is already woven into `rendered` via the
    # `human_notes_content` arg to `render_canonical_body`; no separate
    # insertion step required.

    # --dry-run + --check short-circuit before write.
    if args.dry_run:
        sys.stdout.write(rendered)
        return exit_codes.EXIT_OK

    if args.check:
        existing = existing_md or ""
        if existing == rendered:
            return exit_codes.EXIT_OK
        print(
            f"render_plan: drift detected at {paths.md_path}",
            file=sys.stderr,
        )
        return exit_codes.EXIT_DRIFT

    # Step 9: atomic write to MD path.
    try:
        write_atomic(paths.md_path, rendered)
    except AtomicWriteError as exc:
        _emit_stderr_json({"error": "atomic_write_failed", "message": str(exc)})
        return exit_codes.EXIT_ATOMIC_WRITE_FAILED

    # §B.impl.4 step 10 — render event emission is the CALLER's responsibility
    # (§A chain driver / §E update_orchestrator). §B has no direct emitter per
    # §C.impl.6 KNOWN_WRITERS line 1542. Phase 1: no-op.
    return exit_codes.EXIT_OK


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class _UsageError(ValueError):
    """Argument resolution failed; map to EXIT_USAGE."""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin/render_plan",
        description=(
            "Render <slug>_plan.json (or <slug>_orchestrator.json) into the "
            "canonical MD view. Preserves operator notes inside the "
            "anchor-delimited block. Idempotent."
        ),
    )
    parser.add_argument(
        "slug",
        nargs="?",
        help=(
            "kebab-case plan slug; resolves to docs/plans/<slug>/. "
            "Omit when using --from-json."
        ),
    )
    parser.add_argument(
        "--kind",
        choices=("plan", "orchestrator", "state"),
        default="plan",
        help=(
            "Substrate kind. Defaults to `plan`. `state` renders the "
            "canonical `_orchestrator.md` from `_state.json` (no-slug "
            "filenames; see orch_status_render T3)."
        ),
    )
    parser.add_argument(
        "--from-json",
        type=Path,
        default=None,
        help=(
            "Render an arbitrary JSON path (used by the chain driver's "
            "Call 2 handshake; resolves output MD path adjacent to JSON)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write canonical body to stdout instead of touching the MD file.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Render in-memory and diff against existing MD. Exits 0 on "
            "byte-identical match, exits 11 on drift. Wired into CI."
        ),
    )
    return parser


def _resolve_paths(args: argparse.Namespace) -> RenderPaths:
    kind: PlanKind = args.kind
    if args.from_json is not None:
        json_path = Path(args.from_json).resolve()
        # Derive output MD path: replace .json with .md sibling. For
        # kind="state" the canonical output is `_orchestrator.md` adjacent
        # to `_state.json` (no-slug filename per orch_status_render T3) —
        # we honor that special case here rather than the default
        # `.with_suffix(".md")`.
        if json_path.suffix != ".json":
            raise _UsageError(
                f"--from-json must point to a .json file (got {json_path})"
            )
        if kind == "state":
            md_path = json_path.parent / "_orchestrator.md"
            # Slug derives from the parent dir name (e.g.
            # docs/plans/<slug>/_state.json → <slug>); fall back to the
            # stem when the parent dir name is non-conformant.
            slug = json_path.parent.name or json_path.stem
        else:
            md_path = json_path.with_suffix(".md")
            # Slug derives from filename: <slug>_plan.json → <slug>;
            # fallback to the json stem if the pattern doesn't match
            # (test fixtures).
            stem = json_path.stem
            slug = stem
            for suffix in ("_plan", "_orchestrator"):
                if stem.endswith(suffix):
                    slug = stem[: -len(suffix)]
                    break
        return RenderPaths(
            json_path=json_path, md_path=md_path, slug=slug, kind=kind
        )

    if not args.slug:
        raise _UsageError(
            "either <slug> or --from-json <path> is required"
        )
    slug = args.slug
    plan_dir = _PLANS_DIR / slug
    if kind == "state":
        # No-slug filenames per orch_status_render T3: the state ledger is
        # `_state.json` and its rendered view is `_orchestrator.md`,
        # mirroring the v2.7 §E.2 / §5.B contract.
        json_path = plan_dir / "_state.json"
        md_path = plan_dir / "_orchestrator.md"
        return RenderPaths(
            json_path=json_path, md_path=md_path, slug=slug, kind=kind
        )
    suffix = "_plan" if kind == "plan" else "_orchestrator"
    json_path = plan_dir / f"{slug}{suffix}.json"
    md_path = plan_dir / f"{slug}{suffix}.md"
    return RenderPaths(json_path=json_path, md_path=md_path, slug=slug, kind=kind)


def _emit_stderr_json(payload: dict) -> None:
    """Write a single-line JSON envelope to stderr."""
    sys.stderr.write(json.dumps(payload, sort_keys=False) + "\n")


if __name__ == "__main__":
    sys.exit(main())
