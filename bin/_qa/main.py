"""CLI entry point for `bin/qa`.

Per plan §D.8.3 + the parallel surface of `bin._planner.main`. A single
subcommand `qa` resolves the slug to `docs/plans/<slug>/`, reads the
recon, invokes `invoke_qa(...)`, and writes the resulting MD to
`docs/plans/<slug>/<slug>_qa.md` via §B's
`bin._render_plan.atomic_write.write_atomic`.

The driver-writes-not-subagent invariant (plan §D.6 criterion 5) is
preserved: `invoke_qa` returns the MD text; THIS module writes it.

CLI dispatch:
- `python -m bin._qa.main qa <slug> [--repo-state ...] [--chain-id ...]`

Predecessor gate (per v2.7 §1.C):
- REFUSE if the chosen subject's artifact `<slug>_<subject>.md` does NOT
  exist or is empty (predecessor artifact missing — run the `<subject>`
  step first). `<subject>` is the `--subject` choice (recon / qna /
  research / plan); defaults to `recon`, so a no-flag invocation refuses
  on a missing/empty `<slug>_recon.md` exactly as before.

Output target is SUBJECT-AWARE (qa_review_target_generalization T7). The
BASE output file depends on the chosen `--subject`:
- `recon` (default) → `<slug>_qa.md` (UNCHANGED — historical back-compat).
- non-recon (qna / research / plan) → `<slug>_qa_<subject>.md` (each
  subject its own base file, e.g. `<slug>_qa_plan.md`). This prevents
  cross-subject append-mixing by construction. NOTE the `_qa_` infix: the
  INPUT artifact under review is `<slug>_<subject>.md`; the OUTPUT qa file
  is `<slug>_qa_<subject>.md` — different files.

Re-run modes (replaces the old target-exists refusal). qa never refuses
on an existing base; instead the operator chooses how the re-run lands.
The /qa slash command maps natural language to these flags (and the flags
remain available directly). "<base>" below = the subject's base file
(`<slug>_qa.md` for recon, `<slug>_qa_<subject>.md` otherwise):

- DEFAULT = APPEND: a new adversarial pass is appended to the existing
  `<base>` under a provenance separator. Natural language:
  "update / revise / add to the qa".
- `--new-file` = NEW FILE: the pass is written to the next free
  `<base-stem>_<N>.md` (e.g. `<slug>_qa_2.md` for recon, or
  `<slug>_qa_plan_2.md` for `--subject plan`), leaving the base untouched.
  Natural language: "new file / second pass / separate qa".
- `--reopen` = OVERWRITE: the base `<base>` is replaced in place.
  Natural language redo-synonyms ("redo", "from scratch", "regenerate").
- `--reopen` and `--new-file` are mutually exclusive (usage error).
- First authorship of `<base>` (no prior file) ignores the mode
  and writes the base file.

On a re-run against an existing base, a one-line stderr notice names the
mode + target (`Reopened: …` / `New file: …` / `Appended: …`). A clean
first-run write is silent.

Exit codes per `bin._qa.exit_codes`:
- 0  = success (MD written under `docs/plans/<slug>/`)
- 1  = usage error (slug dir missing, subject artifact
       `<slug>_<subject>.md` missing/empty, directive over cap, or
       --reopen + --new-file together)
- 7  = atomic write failed
- 8  = target_exists_no_reopen — RETAINED in the closed enum for cross-CLI
       parity with `bin/plan`, but qa NO LONGER raises it (re-runs append
       by default; nothing to refuse).
- 17 = SDK call failed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from bin._env_paths import load_env_file

load_env_file()

from . import exit_codes
from .invoke import (
    QaInputs,
    QaResult,
    QaSdkFailed,
    invoke_qa,
)
from .subject import (
    DEFAULT_SUBJECT,
    SUBJECT_CHOICES,
    subject_artifact_name,
)


from bin._env_paths import plans_dir as _env_paths_plans_dir

# The adopter's plan dir, not the plugin's: in installed-plugin mode this file
# sits under the plugin cache, so a `parents[2]` walk resolves the wrong repo.
_PLANS_DIR = _env_paths_plans_dir()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin/qa",
        description=(
            "Single-call qa CLI — adversarial review of <slug>_recon.md "
            "against the deterministically-constructed rubric (plan §D.8.3)."
        ),
    )
    sub = parser.add_subparsers(dest="step", required=True)

    sp = sub.add_parser(
        "qa",
        help="Run the qa pass (single SDK call; MD output).",
    )
    sp.add_argument("slug", help="Plan slug (e.g., property_based_parser_hardening)")
    sp.add_argument(
        "--subject",
        choices=list(SUBJECT_CHOICES),
        default=DEFAULT_SUBJECT,
        help=(
            "Which predecessor artifact to review: one of "
            "recon / qna / research / plan. Resolves to "
            "<slug>_<subject>.md under the plan dir (plan -> "
            "<slug>_plan.md, the rendered MD twin, NOT _plan.json). "
            "Defaults to 'recon', preserving the historical behavior of "
            "reviewing <slug>_recon.md."
        ),
    )
    sp.add_argument(
        "--repo-state",
        default="(no repo-state summary provided)",
        help="Free-form summary of current repo state.",
    )
    sp.add_argument(
        "--directive",
        default=None,
        help=(
            "Optional operator-authored free-text directive (≤ 8KB "
            "UTF-8). Wrapped in <operator-directive>...</operator-"
            "directive> in the user prompt per std_command_operator_"
            "extensions TE."
        ),
    )
    sp.add_argument(
        "--chain-id",
        default=None,
        help="Optional chain id for forensic logging.",
    )
    sp.add_argument(
        "--stdout",
        action="store_true",
        help=(
            "Emit the resulting MD to stdout instead of writing the "
            "<slug>_qa.md file. Useful for diff-review."
        ),
    )
    sp.add_argument(
        "--reopen",
        action="store_true",
        help=(
            "Re-run mode = OVERWRITE: replace an existing <slug>_qa.md in "
            "place. Mutually exclusive with --new-file. On a real overwrite "
            "(base existed) a `Reopened: overwrote <abs-path>` notice is "
            "emitted to stderr. The /qa slash command maps natural-language "
            "redo-synonyms ('redo', 'from scratch') to this flag."
        ),
    )
    sp.add_argument(
        "--new-file",
        dest="new_file",
        action="store_true",
        help=(
            "Re-run mode = NEW FILE: write the review to the next free "
            "<slug>_qa_<N>.md (qa_2, qa_3, …) instead of touching the base. "
            "Mutually exclusive with --reopen. The /qa slash command maps "
            "natural-language 'new file' / 'second pass' to this flag. The "
            "default re-run mode, when neither flag is set, is APPEND."
        ),
    )

    return parser


def _resolve_plan_dir(slug: str) -> Path:
    """Resolve and validate the plan directory."""
    plan_dir = _PLANS_DIR / slug
    if not plan_dir.exists():
        raise _UsageError(
            f"plan directory does not exist: {plan_dir} "
            f"(create it before invoking qa)"
        )
    if not plan_dir.is_dir():
        raise _UsageError(f"{plan_dir} is not a directory")
    return plan_dir


class _UsageError(ValueError):
    """Raised for CLI usage errors mapped to exit code 1."""


class _TargetExistsNoReopenError(_UsageError):
    """Raised when `<slug>_qa.md` already exists and `--reopen` was not set.

    Subclass of `_UsageError` so callers that catch the parent type still
    handle this case structurally (mirrors the planner-side pattern from
    std_command_operator_extensions TA). The `main()` dispatch checks
    for this subclass first to return the distinct exit code 8
    (`EXIT_TARGET_EXISTS_NO_REOPEN`).
    """


def _emit_stderr_json(payload: dict[str, Any]) -> None:
    """Emit a structured-error JSON envelope to stderr.

    Mirrors §B's error-envelope convention so chain-driver callers can
    parse `$STDERR` uniformly across the planner + qa CLI surface.
    """
    print(json.dumps(payload), file=sys.stderr)


_DIRECTIVE_MAX_BYTES = 8192
"""SC10: hard cap on `--directive` UTF-8 byte length. 8KB is generous
for "how/constraints" prose and well under any context-window concern.
Mirrors the same constant on the planner side."""


def _build_inputs(plan_dir: Path, slug: str, args: argparse.Namespace) -> QaInputs:
    """Read inputs from `plan_dir` and pack into a `QaInputs`.

    Per v2.7 §1.C:
    - REFUSE if the chosen subject's artifact `<slug>_<subject>.md` is
      missing or empty (predecessor gate). The refusal text names the
      chosen `<subject>` (recon / qna / research / plan); `recon` is the
      default, preserving the historical recon-only behavior.

    There is NO target-exists refusal: an existing `<slug>_qa.md` is no
    longer a hard stop. The re-run mode (append / new-file / overwrite) is
    resolved at write time in `main()` via `_resolve_target_and_body`.

    Per SC10: refuse if `--directive` UTF-8 length exceeds 8KB.
    """
    # Subject-aware predecessor resolution (qa_review_target_generalization
    # T1). `subject` defaults to "recon" so a no-flag invocation resolves
    # the exact `<slug>_recon.md` path the CLI resolved before this enum
    # existed. The path shape is uniform `<slug>_<subject>.md`; for the
    # `plan` subject this is the rendered `<slug>_plan.md` MD twin, NOT the
    # `_plan.json` substrate.
    #
    # T1 introduced `subject_path` + set the `subject` selector on
    # QaInputs. T2 generalizes the refusal WORDING below to name the chosen
    # subject instead of hard-saying "recon": a `--subject qna` on a missing
    # artifact now refuses with qna-worded text, not a recon-worded message
    # that happens to interpolate the correct path. The resolved-path
    # interpolation T1 added is preserved.
    #
    # `getattr` default mirrors the defensive `getattr(args, "reopen",
    # False)` pattern already used in `_resolve_rerun_mode` — a Namespace
    # built without `--subject` (e.g. a direct programmatic caller) still
    # resolves the recon default rather than raising AttributeError.
    subject = getattr(args, "subject", DEFAULT_SUBJECT)
    subject_path = plan_dir / subject_artifact_name(slug, subject)
    if not subject_path.exists():
        raise _UsageError(
            f"{subject} artifact does not exist: {subject_path} "
            f"(run the {subject} step first to produce it)"
        )
    subject_body = subject_path.read_text(encoding="utf-8")
    if not subject_body.strip():
        raise _UsageError(
            f"{subject} artifact is empty: {subject_path}"
        )

    # Re-run modes (append / new-file / overwrite) are resolved at write
    # time in `main()` via `_resolve_target_and_body` — there is no longer a
    # target-exists *refusal*. Default re-run mode is APPEND; --new-file
    # writes <slug>_qa_<N>.md; --reopen overwrites the base. The
    # `_TargetExistsNoReopenError` class + exit code 8 are retained in the
    # closed enum for cross-CLI parity with the planner but are not raised.
    directive = args.directive
    if directive is not None:
        actual = len(directive.encode("utf-8"))
        if actual > _DIRECTIVE_MAX_BYTES:
            raise _UsageError(
                f"directive exceeds 8KB limit ({actual} bytes); "
                f"shorten or split"
            )

    return QaInputs(
        # `subject_body` holds the chosen subject's artifact body (recon by
        # default); `subject_findings` is the subject-agnostic content field.
        subject_findings=subject_body,
        repo_state_summary=args.repo_state,
        subject=subject,
        directive=directive,
    )


def _qa_output_stem(slug: str, subject: str) -> str:
    """Return the subject-aware base STEM for qa output files.

    qa_review_target_generalization T7 — subject-stamped output routing.
    The historical (recon) path is preserved bit-for-bit; every non-recon
    subject gets its own base file so adversarial passes never append-mix
    across subjects:

    - ``subject == "recon"`` → ``<slug>_qa``       (UNCHANGED back-compat;
      base file ``<slug>_qa.md``, numbered ``<slug>_qa_<N>.md``).
    - non-recon subject       → ``<slug>_qa_<subject>``  (base file
      ``<slug>_qa_<subject>.md`` e.g. ``<slug>_qa_plan.md``; numbered
      ``<slug>_qa_<subject>_<N>.md``).

    NAMING CAUTION: the INPUT artifact under review is
    ``<slug>_<subject>.md`` (via :func:`subject_artifact_name`); the OUTPUT
    qa file resolved here is ``<slug>_qa_<subject>.md`` — note the ``_qa_``
    infix. They are deliberately different files; do not conflate.
    """
    if subject == DEFAULT_SUBJECT:  # "recon" — historical default path
        return f"{slug}_qa"
    return f"{slug}_qa_{subject}"


def _output_target(plan_dir: Path, slug: str, subject: str = DEFAULT_SUBJECT) -> Path:
    """Resolve the BASE qa output path for ``subject``.

    recon → ``<slug>_qa.md`` (unchanged); non-recon → ``<slug>_qa_<subject>.md``.
    Defaults to the recon stem so existing call sites that pre-date the
    subject enum keep resolving ``<slug>_qa.md``.
    """
    return plan_dir / f"{_qa_output_stem(slug, subject)}.md"


def _write_output(target: Path, body: str) -> None:
    """Atomic-write the qa MD output to `target`."""
    from bin._render_plan.atomic_write import write_atomic

    if not body.endswith("\n"):
        body = body + "\n"
    write_atomic(target, body)


_QA_APPEND_SEPARATOR = "\n\n<!-- ───── qa re-run (appended) ───── -->\n\n"
"""Provenance marker inserted between stacked qa passes in APPEND mode so a
reader — and the downstream planner, which now ingests the whole file — can
see where one adversarial pass ends and the next begins."""


def _resolve_rerun_mode(args: argparse.Namespace) -> str:
    """Map the mode flags to one of: 'overwrite' | 'new-file' | 'append'.

    Default (neither flag) is 'append'. The two flags are mutually
    exclusive; setting both raises `_UsageError` (exit code 1).
    """
    reopen = getattr(args, "reopen", False)
    new_file = getattr(args, "new_file", False)
    if reopen and new_file:
        raise _UsageError(
            "--reopen (overwrite) and --new-file are mutually exclusive; "
            "pick one (or neither, for the default append mode)"
        )
    if reopen:
        return "overwrite"
    if new_file:
        return "new-file"
    return "append"


def _next_numbered_target(
    plan_dir: Path, slug: str, subject: str = DEFAULT_SUBJECT
) -> Path:
    """Return the next free ``<stem>_<N>.md`` for ``subject`` (N starts at 2).

    The stem is subject-aware (``<slug>_qa`` for recon; ``<slug>_qa_<subject>``
    otherwise — see :func:`_qa_output_stem`), so new-file re-runs number
    within the chosen subject's family and never collide across subjects.
    """
    stem = _qa_output_stem(slug, subject)
    n = 2
    while (plan_dir / f"{stem}_{n}.md").exists():
        n += 1
    return plan_dir / f"{stem}_{n}.md"


def _resolve_target_and_body(
    plan_dir: Path,
    slug: str,
    mode: str,
    new_body: str,
    subject: str = DEFAULT_SUBJECT,
) -> tuple[Path, str]:
    """Resolve the write target + final body for the chosen re-run mode.

    Pure (no SDK / no network) so it is unit-testable directly. The base
    target is SUBJECT-AWARE (qa_review_target_generalization T7): recon
    resolves the historical ``<slug>_qa.md``; a non-recon subject resolves
    ``<slug>_qa_<subject>.md`` (its own file). The first authorship of the
    subject's base (no prior base) ignores ``mode`` and writes the base
    file; ``mode`` only differentiates re-runs against an existing base.

    - 'overwrite' → (base, new_body)
    - 'new-file'  → (next free <stem>_<N>.md, new_body)
    - 'append'    → (base, existing + separator + new_body)

    Because each subject owns its own base file, APPEND never mixes a recon
    pass into a plan pass (or vice versa) — the separation is by
    construction, not by the in-file separator.
    """
    base = _output_target(plan_dir, slug, subject)
    if not base.exists():
        return base, new_body
    if mode == "overwrite":
        return base, new_body
    if mode == "new-file":
        return _next_numbered_target(plan_dir, slug, subject), new_body
    existing = base.read_text(encoding="utf-8")
    if existing and not existing.endswith("\n"):
        existing += "\n"
    return base, existing + _QA_APPEND_SEPARATOR + new_body


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exit_codes.EXIT_USAGE if exc.code != 0 else exit_codes.EXIT_OK

    try:
        plan_dir = _resolve_plan_dir(args.slug)
    except _UsageError as exc:
        _emit_stderr_json({"error": "usage", "detail": str(exc)})
        return exit_codes.EXIT_USAGE

    # Resolve the re-run mode (append default / new-file / overwrite) up
    # front so a bad flag combo (--reopen + --new-file) fails before the
    # SDK call.
    try:
        mode = _resolve_rerun_mode(args)
    except _UsageError as exc:
        _emit_stderr_json({"error": "usage", "detail": str(exc)})
        return exit_codes.EXIT_USAGE

    # Resolve the review subject up front (recon default) so the base-target
    # resolution + the "did the base already exist?" check key on the same
    # subject-aware filename the SDK output will be written to
    # (qa_review_target_generalization T7).
    _subject = getattr(args, "subject", DEFAULT_SUBJECT)

    # Did the subject's BASE qa file already exist? Drives the post-write
    # stderr notice — a clean first-run write is silent; only re-runs against
    # an existing base announce which mode ran. recon keys on <slug>_qa.md;
    # a non-recon subject keys on <slug>_qa_<subject>.md.
    _base_existed_before = (
        not getattr(args, "stdout", False)
        and _output_target(plan_dir, args.slug, _subject).exists()
    )

    try:
        inputs = _build_inputs(plan_dir, args.slug, args)
    except _UsageError as exc:
        _emit_stderr_json({"error": "usage", "detail": str(exc)})
        return exit_codes.EXIT_USAGE

    try:
        result: QaResult = invoke_qa(
            slug=args.slug,
            inputs=inputs,
            chain_id=args.chain_id,
        )
    except QaSdkFailed as exc:
        _emit_stderr_json(
            {
                "error": "qa_sdk_failed",
                "detail": exc.detail,
                "last_response_chars": (
                    len(exc.last_response) if exc.last_response else 0
                ),
            }
        )
        return exit_codes.EXIT_SDK_CALL_FAILED

    if args.stdout:
        sys.stdout.write(result.qa_md)
        if not result.qa_md.endswith("\n"):
            sys.stdout.write("\n")
        return exit_codes.EXIT_OK

    target, body = _resolve_target_and_body(
        plan_dir, args.slug, mode, result.qa_md, _subject
    )
    try:
        _write_output(target, body)
    except Exception as exc:  # noqa: BLE001 — wrap any atomic-write failure
        _emit_stderr_json(
            {"error": "atomic_write_failed", "target": str(target), "detail": str(exc)}
        )
        return exit_codes.EXIT_ATOMIC_WRITE_FAILED

    # Re-run stderr notice — names the mode + resolved target so the operator
    # sees what landed. A clean first-run write (no prior base) is silent.
    if _base_existed_before:
        if mode == "overwrite":
            print(f"Reopened: overwrote {target.resolve()}", file=sys.stderr)
        elif mode == "new-file":
            print(f"New file: wrote {target.resolve()}", file=sys.stderr)
        else:  # append
            print(f"Appended: extended {target.resolve()}", file=sys.stderr)

    return exit_codes.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
