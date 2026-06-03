"""CLI entry point for `bin/plan` and `bin/implplan`.

Per implplan §D.impl.2 + §D.impl.11 #2 ratification: a single CLI module
backs both the chain-driver-side Python callable (`invoke_planner`) and
the operator-direct slash commands (`/plan`, `/implplan`).

CLI dispatch:
- `python -m bin._planner.main plan <slug> [...]`
- `python -m bin._planner.main implplan <slug> [...]`

Both subcommands resolve the slug to `docs/plans/<slug>/`, read recon /
qa / research / lessons inputs from that directory, invoke
`invoke_planner(...)`, and write the resulting JSON dict to
`docs/plans/<slug>/<slug>_plan.json` (or `<slug>_orchestrator.json`)
via §B's `bin/_render_plan/atomic_write.write_atomic`.

The driver-writes-not-subagent invariant (plan §D.6 criterion 5) is
preserved here: `invoke_planner` returns the dict; THIS module writes
it to disk. The chain driver (§A.impl) is the other writer.

Exit codes per implplan §A.impl.3a (`bin/_planner/exit_codes.py`):
- 0  = success
- 1  = usage error
- 7  = atomic write failed
- 8  = target_exists_no_reopen (operator must pass --reopen, or for a
       cascade-blocked reopen the downstream orchestrator.json must be
       deleted first)
- 16 = SDK retry exhausted (verify_plan_rejected family)

--reopen semantics (per std_command_operator_extensions TA + plan §D
operator-extensions track + research R3/R5 single-canonical-verb rule):

- Bare `bin/plan <slug>` against an existing `<slug>_plan.json` refuses
  with exit code 8; the operator must add `--reopen` to overwrite.
- `bin/plan --reopen <slug>` overwrites `<slug>_plan.json` UNLESS the
  downstream `<slug>_orchestrator.json` also exists, in which case the
  CLI refuses with exit 8 and instructs the operator to delete the
  orchestrator first (single-step --reopen does NOT auto-cascade per
  QA §C.2). `bin/implplan --reopen <slug>` is unaffected by the cascade
  gate because the orchestrator.json IS its own target.
- On successful overwrite, a `Reopened: overwrote <abs-path>` notice is
  emitted to stderr so the operator sees the destructive action was
  taken intentionally.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

load_dotenv(dotenv_path=find_dotenv(filename=".env"))

from . import exit_codes
from .patch_apply import (
    PatchApplyError,
    PatchBoundExceeded,
    PatchIntegrityError,
    PatchPostApplyInvalid,
    apply_patch,
)
from .reconcile import (
    ReconciliationRefused,
    reconcile_active_plan,
    sync_reconciliation_to_orchestrator,
)
from .two_call import (
    PlannerEmissionExhausted,
    PlannerInputs,
    PlannerResult,
    invoke_planner,
)

# `closed_state.is_closed` (T4) is the CLOSED-plan predicate for T5d's deny
# gate; `is_active_plan` resolves "orchestrator exists, NOT closed" for the
# reconcile branch. Imported at module top — `reconcile` keeps the heavier
# orchestrator-query I/O behind lazy imports, so this stays import-light.
from bin._update_orchestrator.closed_state import is_closed as _orchestrator_is_closed

# Lazy-import §B's atomic write to keep the CLI import-light when only
# `invoke_planner` is used programmatically.

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLANS_DIR = _REPO_ROOT / "docs" / "plans"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin/plan",
        description=(
            "Two-call planner CLI. Subcommands: 'plan' (recon/qa/research → "
            "plan substrate) and 'implplan' (plan → orchestrator substrate)."
        ),
    )
    sub = parser.add_subparsers(dest="step", required=True)

    for step in ("plan", "implplan"):
        sp = sub.add_parser(
            step,
            help=f"Run the {step} step (Call 1 reasoning + Call 2 emission)",
        )
        sp.add_argument("slug", help="Plan slug (e.g., brand_handoff_gate)")
        sp.add_argument(
            "--tier",
            default="Tier 2",
            help="Work-sizing tier passed to Call 1 system prompt (default: 'Tier 2').",
        )
        sp.add_argument(
            "--repo-state",
            default="(no repo-state summary provided)",
            help="Free-form summary of current repo state.",
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
                "Emit the resulting JSON to stdout instead of writing the "
                "<slug>_*.json file. Useful for diff-review."
            ),
        )
        sp.add_argument(
            "--reopen",
            action="store_true",
            help=(
                "Allow overwriting an existing target artifact "
                "(<slug>_plan.json for `plan`, <slug>_orchestrator.json "
                "for `implplan`). Without this flag, the CLI refuses with "
                "exit code 8 (target_exists_no_reopen) so accidental "
                "overwrites are impossible. Note: single-step --reopen does "
                "NOT auto-cascade — re-running `plan --reopen` when the "
                "downstream <slug>_orchestrator.json also exists still "
                "refuses; the operator must delete the orchestrator first."
            ),
        )
        sp.add_argument(
            "--amend",
            action="store_true",
            help=(
                "Surgically AMEND an existing target artifact "
                "(<slug>_plan.json for `plan`) via a keyed patch op-list "
                "instead of regenerating it wholesale. INVERSE of the "
                "default create gate: --amend REQUIRES the target to "
                "already exist (a missing <slug>_plan.json refuses with "
                "exit 1, usage). Unlike --reopen, --amend does NOT trigger "
                "the downstream-orchestrator cascade refusal — you amend a "
                "plan precisely when it has already been promoted to an "
                "orchestrator (operator-decision bypass). --amend and "
                "--reopen are mutually exclusive (opposite intents: patch "
                "vs. wholesale overwrite); passing both refuses with exit 1 "
                "(plan_surgical_amend §SC6)."
            ),
        )
        sp.add_argument(
            "--directive",
            default=None,
            help=(
                "Optional operator-authored free-text directive (≤ 8KB "
                "UTF-8). Wrapped in <operator-directive>...</operator-"
                "directive> in Call 1's user prompt per std_command_"
                "operator_extensions TD. Exceeding 8192 bytes refuses "
                "with exit 1 (usage) before any SDK call."
            ),
        )

    return parser


def _read_optional_md(plan_dir: Path, name: str) -> str:
    """Read a per-slug input MD file, returning empty string if missing.

    Examples: `<slug>_recon.md`, `<slug>_qa.md`, `<slug>_research.md`.
    """
    path = plan_dir / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _natural_key(name: str) -> list:
    """Natural sort key so `<slug>_recon_2.md` sorts before `_10.md`."""
    return [int(tok) if tok.isdigit() else tok for tok in re.split(r"(\d+)", name)]


def _read_md_group(plan_dir: Path, slug: str, stem: str) -> str:
    """Read `<slug>_<stem>.md` PLUS any NUMBERED follow-up variants and concatenate.

    A re-run of an exploratory command (`/recon`, `/research`, `/qa`) can
    produce a numbered follow-up file (`<slug>_recon_2.md`, `_3`, …) — the
    "new file" re-run mode — typically responding to a `<slug>_plan.md` with
    a `## Recommendations for /plan` (or `/implplan`) section. This reader
    ingests the base artifact AND every NUMERIC variant `<slug>_<stem>_<N>.md`
    (N = one or more digits) so those recommendations actually reach the
    planner; a subsequent `/plan <slug> --reopen` then folds the adjustments
    in. This is the code half of the "exploratory agents route adjustments
    through their own MD" contract — the plan/orchestrator JSON stays editable
    only by the planner.

    ONLY numbered `_<N>.md` variants are ingested. The documented "new-file
    re-run" convention is always numeric (`_recon_2.md`, `_3`, …), so a
    numeric-only filter matches the real invariant across every stem. This
    deliberately EXCLUDES subject-stamped qa outputs (e.g. `<slug>_qa_plan.md`,
    `<slug>_qa_qna.md`, `<slug>_qa_research.md`) — those carry a non-numeric
    suffix and are the product of running `/qa --subject <subject>` against a
    successor artifact. Subject-stamped qa output is OPERATOR-FOLDED (fed back
    manually via `/plan --reopen`), NOT auto-ingested into the next planner
    run's `<qa-findings>` — auto-ingesting it would loop a qa-on-plan critique
    straight back into the plan it critiqued. The numeric filter is what makes
    that exclusion mechanical: a wildcard `<slug>_<stem>_*.md` would match
    `_qa_plan`, but `<slug>_<stem>_<N>.md` does not.

    Files are concatenated base-first, then numeric variants in natural-numeric
    order, each prefixed with an HTML-comment provenance marker so the planner
    LLM can attribute each block to its source file. Returns "" if none exist.
    """
    parts: list[tuple[str, str]] = []
    base = plan_dir / f"{slug}_{stem}.md"
    if base.exists():
        parts.append((base.name, base.read_text(encoding="utf-8")))
    # Number-restricted variant match: only `<slug>_<stem>_<N>.md` (N = digits).
    # Keep the cheap glob to enumerate candidates, then filter to numeric
    # suffixes so non-numeric (subject-stamped) variants like `_qa_plan.md`
    # are excluded.
    variant_re = re.compile(rf"^{re.escape(slug)}_{re.escape(stem)}_(\d+)\.md$")
    extras = sorted(
        (
            p
            for p in plan_dir.glob(f"{slug}_{stem}_*.md")
            if p.is_file() and variant_re.match(p.name)
        ),
        key=lambda p: _natural_key(p.name),
    )
    for p in extras:
        parts.append((p.name, p.read_text(encoding="utf-8")))
    if not parts:
        return ""
    return "\n\n".join(
        f"<!-- ───── {name} ───── -->\n{body}" for name, body in parts
    )


def _read_lessons(plan_dir: Path) -> str:
    """Read `lessons.md` per slug via `bin/lessons query --json`.

    Per F-02 of §M mid-section review 2026-05-21: §M.impl.5 prescribes
    invoking `bin/lessons query --slug <slug> --json` so that lenient
    parsing drops malformed H2 blocks before they reach the planner LLM.
    On any failure (file missing, subprocess error, malformed output)
    return an empty string — the planner runs without lessons context
    rather than blocking on infrastructure.
    """
    slug = plan_dir.name
    bin_path = plan_dir.parent.parent.parent / "bin" / "lessons"
    if not bin_path.exists():
        return _read_optional_md(plan_dir, "lessons.md")
    import subprocess
    try:
        proc = subprocess.run(
            [str(bin_path), "query", "--slug", slug, "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout


def _read_prior_plan_json(plan_dir: Path, slug: str) -> str | None:
    """Read `<slug>_plan.json` for the implplan step.

    Returns None if missing — the CLI surfaces a usage error in that case
    (implplan step requires a plan substrate to exist).
    """
    plan_json = plan_dir / f"{slug}_plan.json"
    if not plan_json.exists():
        return None
    return plan_json.read_text(encoding="utf-8")


def _resolve_plan_dir(slug: str) -> Path:
    """Resolve and validate the plan directory."""
    plan_dir = _PLANS_DIR / slug
    if not plan_dir.exists():
        raise _UsageError(
            f"plan directory does not exist: {plan_dir}"
            f" (create it before invoking the planner)"
        )
    if not plan_dir.is_dir():
        raise _UsageError(f"{plan_dir} is not a directory")
    return plan_dir


class _UsageError(ValueError):
    """Raised for CLI usage errors mapped to exit code 1."""


class _TargetExistsNoReopenError(_UsageError):
    """Raised when the target output already exists and `--reopen` was not set.

    Subclass of `_UsageError` so callers that catch the parent type still
    handle this case structurally (the orchestrator TA test fixture catches
    `_UsageError` and asserts the stderr names `--reopen`). The `main()`
    dispatch checks for this subclass first to return the distinct exit
    code 8 (`EXIT_TARGET_EXISTS_NO_REOPEN`).
    """


def _emit_stderr_json(payload: dict[str, Any]) -> None:
    """Emit a structured-error JSON envelope to stderr.

    Mirrors §B's error-envelope convention so chain-driver callers can
    parse `$STDERR` uniformly across the chain-orchestrated CLI surface.
    """
    print(json.dumps(payload), file=sys.stderr)


_DIRECTIVE_MAX_BYTES = 8192
"""SC10: hard cap on `--directive` UTF-8 byte length. 8KB is generous
for "how/constraints" prose and well under any context-window concern.
Mirrors the same constant on the qa side
(`bin._qa.main._DIRECTIVE_MAX_BYTES`)."""


# ---------------------------------------------------------------------------
# T6g — per-amend audit log (`<slug>_amend_log.jsonl`) + "Amended:" stderr
# notice + staged plan.md --amend parsing artifact (sealed file untouched).
# ---------------------------------------------------------------------------


def _amend_log_path(plan_dir: Path, slug: str) -> Path:
    """Canonical per-slug append-only audit log location.

    Documented per plan_surgical_amend §SC6 (T6g): one JSONL row per
    `--amend` invocation, schema `{timestamp, directive, ops, result}`,
    APPEND-ONLY (never truncated). The file lives beside the plan it
    amends so it travels with the slug dir and is discoverable without
    a registry lookup.
    """
    return plan_dir / f"{slug}_amend_log.jsonl"


def _append_amend_audit_row(
    plan_dir: Path,
    slug: str,
    *,
    directive: str | None,
    ops: list,
    result: str,
    detail: str | None = None,
) -> None:
    """Append one JSONL row to the per-slug amend audit log.

    plan_surgical_amend §SC6 (T6g). Mandatory audit trail for the surgical
    --amend dispatch: each invocation appends ONE row carrying the
    timestamp, the operator directive (if any), the ORDERED op-list the
    Call-2 emission produced, and the outcome label. APPEND-ONLY: open
    with mode "a" so a prior log is preserved verbatim and never
    truncated (the C8 defeat-by-many-small-amends accountability surface
    only works if every amend is recorded).

    `result` is a short closed string: ``"applied"`` for a clean amend,
    or a failure label (``"apply_failed"``, ``"render_failed_rolled_back"``,
    ``"sdk_exhausted"``, ``"post_apply_invalid"``, ``"bound_exceeded"``,
    ``"integrity"``) for the loud-failure paths. `ops` is the patch's
    ordered op-list (or ``[]`` if Call 2 never emitted, e.g. SDK
    exhaustion). `detail` is an optional human-readable string for
    forensics.

    The append is best-effort with respect to its own filesystem failures
    (a transient EIO must not mask the actual CLI exit code): exceptions
    are swallowed silently. The CLI exit decision is owned by the caller.
    """
    log_path = _amend_log_path(plan_dir, slug)
    row: dict[str, Any] = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "directive": directive,
        "ops": list(ops),
        "result": result,
    }
    if detail is not None:
        row["detail"] = detail
    try:
        # Mode "a" (append, create if missing). Never "w" — that would
        # TRUNCATE a prior log and silently destroy the audit trail.
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        # Best-effort: don't mask the CLI's real exit code with an
        # audit-log IO failure. The amend's success/failure semantics live
        # in `main()`'s return value, not in this side-channel.
        pass


def _format_amended_keys(ops: list) -> str:
    """Build a compact "Amended: <field/key>[, ...]" stderr-notice payload.

    For each op in `ops` (in array order — the same determinism contract
    `patch_apply.apply_patch` honors), emit a friendly key such as
    ``"success_criterion[SC2]"`` / ``"task[T1]"`` / ``"component[Foo]"`` /
    ``"reference[(recon, foo)]"`` / ``"non_goal[3]"`` / ``"scalar:title"``.
    The order matches the op-list order so the operator sees what was
    touched in the sequence it was applied.
    """
    parts: list[str] = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        op_kind = op.get("op_kind")
        addr = op.get("address") or {}
        if op_kind in {"success_criterion", "task"}:
            parts.append(f"{op_kind}[{addr.get('id')}]")
        elif op_kind == "component":
            parts.append(f"{op_kind}[{addr.get('name')}]")
        elif op_kind == "reference":
            parts.append(
                f"{op_kind}[({addr.get('kind')}, {addr.get('pointer')})]"
            )
        elif op_kind == "non_goal":
            # `add` non_goal ops have no meaningful index; show action.
            idx = addr.get("index")
            action = op.get("action")
            if idx is None:
                parts.append(f"{op_kind}[{action}]")
            else:
                parts.append(f"{op_kind}[{idx}]")
        elif op_kind == "scalar":
            parts.append(f"scalar:{addr.get('field')}")
        else:
            parts.append(str(op_kind))
    return ", ".join(parts) if parts else "(no ops)"


# Staged `.claude/commands/plan.md` --amend parsing artifact. The sealed
# `.claude/commands/plan.md` file is sealed-path (.claude/hooks/sealed_paths.txt)
# so the automated build CANNOT edit it directly. T6g STAGES the proposed
# --amend parsing edit (an additive section to plan.md) + an apply script the
# operator runs once to apply it, keeping the sealed file untouched by the
# build. The constants below hold the staged content so the test
# (`test_plan_md_amend_staged.py`) and the operator-applied step share a
# single source of truth.

_STAGED_PLAN_MD_AMEND_SNIPPET = """\
## Parse the operator's tail for --amend

When `$ARGUMENTS` contains the literal `--amend` token, route the
remaining tail (after slug + flag) through the substrate as the amend
directive:

```bash
bin/plan <slug> --amend --directive "<remaining-prose>"
```

The substrate (`bin/_planner/main.py`) treats `--amend` as the INVERSE
gate of the default create gate: a missing `<slug>_plan.json` refuses
with exit 1 (usage), and `--amend` and `--reopen` are mutually
exclusive. On success, an `Amended: <field/key>` notice fires on
stderr and one JSONL row is appended to
`docs/plans/<slug>/<slug>_amend_log.jsonl` (the per-slug audit log,
schema: timestamp + directive + ordered op-list + result).

The auto-memory `feedback_plan_reopen_surgical_limits` (alias `ln`)
captures the prior friction (`/tmp+cp` for surgical edits on active
plans); `bin/plan --amend` is the sanctioned replacement.
"""

_STAGED_PLAN_MD_APPLY_SCRIPT = """\
#!/bin/bash
# Operator-applied apply script for the plan_surgical_amend T6g staged
# `.claude/commands/plan.md` edit. The sealed-paths hook blocks the
# automated build from editing this sealed file directly; the operator
# runs THIS script once to apply the staged --amend parsing section.
#
# Usage (from repo root):
#   bash <staged-dir>/plan_md_amend_apply.sh [--dry-run]
#
# `--dry-run` prints the diff that WOULD be applied without writing.
set -euo pipefail

SCRIPT_DIR="$( cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd )"
SNIPPET_PATH="${SCRIPT_DIR}/plan_md_amend_snippet.md"
SEALED_TARGET=".claude/commands/plan.md"

if [ ! -f "${SNIPPET_PATH}" ]; then
  echo "missing staged snippet: ${SNIPPET_PATH}" >&2
  exit 1
fi
if [ ! -f "${SEALED_TARGET}" ]; then
  echo "missing sealed target: ${SEALED_TARGET}" >&2
  exit 1
fi

if [ "${1:-}" = "--dry-run" ]; then
  echo "--- staged snippet ---"
  cat "${SNIPPET_PATH}"
  echo "--- would append to ${SEALED_TARGET} ---"
  exit 0
fi

# Idempotency: refuse to double-apply if the marker is already in the
# sealed file.
if grep -qF "Parse the operator's tail for --amend" "${SEALED_TARGET}"; then
  echo "already applied: ${SEALED_TARGET} already contains the --amend parsing section" >&2
  exit 0
fi

# Append the snippet to the sealed file (the operator session has write
# permission on the sealed file; the automated build does not).
printf '\\n' >> "${SEALED_TARGET}"
cat "${SNIPPET_PATH}" >> "${SEALED_TARGET}"
echo "applied: appended ${SNIPPET_PATH} to ${SEALED_TARGET}"
"""


def stage_plan_md_amend_artifacts(target_dir: Path) -> dict[str, Path]:
    """Materialize the staged plan.md --amend artifacts under `target_dir`.

    plan_surgical_amend §SC6 (T6g). Writes the proposed `.claude/commands/
    plan.md` --amend parsing snippet AND an executable apply script the
    operator runs once to apply it to the sealed file. The automated build
    cannot touch `.claude/commands/plan.md` directly (sealed-path), so this
    helper packages the edit as the operator-applied artifact pair the
    `feedback_operator_commands_as_scripts` memory prescribes (an
    invocable `bash <staged-dir>/plan_md_amend_apply.sh` with a
    diff-preview).

    Returns a mapping with two `Path` entries:
      * ``"snippet"`` — the proposed plan.md addition;
      * ``"apply"``   — the apply script (mode 0o755 — invocable).

    Does NOT touch the sealed `.claude/commands/plan.md` itself: that is
    the operator's job at apply-time. Idempotent w.r.t. its own files: a
    re-run overwrites the staged artifacts to keep them in sync with the
    in-repo constants above.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    snippet_path = target_dir / "plan_md_amend_snippet.md"
    apply_path = target_dir / "plan_md_amend_apply.sh"
    snippet_path.write_text(_STAGED_PLAN_MD_AMEND_SNIPPET, encoding="utf-8")
    apply_path.write_text(_STAGED_PLAN_MD_APPLY_SCRIPT, encoding="utf-8")
    # Mode 0o755 so the operator can `bash <path>` it without chmod gymnastics
    # (per feedback_operator_commands_as_scripts: ship an invocable command).
    apply_path.chmod(0o755)
    return {"snippet": snippet_path, "apply": apply_path}


def _build_inputs(plan_dir: Path, slug: str, args: argparse.Namespace) -> PlannerInputs:
    """Read inputs from `plan_dir` and pack into a `PlannerInputs`.

    Per std_command_operator_extensions TA: refuses with
    `_TargetExistsNoReopenError` (→ exit code 8) when the target output
    artifact already exists and `--reopen` was not passed. When emitting
    to stdout (`--stdout`), the target is not actually written so the
    check is skipped. For the `plan` step, an additional cascade-refusal
    fires even with `--reopen` if the downstream
    `<slug>_orchestrator.json` exists — single-step --reopen does NOT
    auto-cascade (per QA §C.2 + research R3/R5).

    Per plan_surgical_amend §SC6 (T6a) `--amend` runs the INVERSE gate:
    a surgical amend REQUIRES the target `<slug>_plan.json` to already
    exist (a missing target refuses with `_UsageError` → exit 1, the
    exact inverse of the create gate's target-EXISTS refusal). Because an
    amend operates on an already-authored plan, it deliberately does NOT
    apply the create-target-exists refusal NOR the --reopen
    downstream-orchestrator cascade refusal — you amend a plan precisely
    when it has already been promoted to an orchestrator
    (operator-decision bypass). `--amend` and `--reopen` are mutually
    exclusive (opposite intents); passing both refuses with `_UsageError`
    (exit 1). At T6a this gate only DECIDES "allow amend"; the actual
    patch-mode Call-2 + apply dispatch is wired by T6b–T6f.
    """
    stdout_mode = getattr(args, "stdout", False)
    reopen = getattr(args, "reopen", False)
    amend = getattr(args, "amend", False)

    # `--amend` and `--reopen` are mutually exclusive: a surgical patch and
    # a wholesale overwrite are opposite intents on the same target. Refuse
    # the contradictory combination up front (usage, exit 1) before any
    # filesystem decision so the operator gets a single clear diagnostic.
    if amend and reopen:
        raise _UsageError(
            "--amend and --reopen are mutually exclusive: --amend "
            "surgically patches an existing plan, --reopen regenerates it "
            "wholesale. Pass exactly one."
        )

    if amend:
        # INVERSE gate (plan_surgical_amend §SC6 / T6a). A surgical amend
        # requires the target to ALREADY exist — the exact inverse of the
        # create gate below. A missing target is a usage error (exit 1),
        # NOT a target-exists refusal (exit 8): there is nothing to amend.
        # The --stdout diff-review mode still requires the prior plan to
        # exist (you cannot patch what isn't there), so this check is NOT
        # skipped under --stdout — unlike the create gate, which is.
        target = _output_target(plan_dir, slug, args.step)
        if not target.exists():
            raise _UsageError(
                f"--amend requires an existing target to patch, but "
                f"{target} does not exist; run `bin/plan {slug}` to create "
                f"it first (--amend is the INVERSE of the create gate: it "
                f"refuses when the target is ABSENT, not when it exists)"
            )
        # Deliberately fall through WITHOUT applying the create
        # target-exists refusal OR the --reopen downstream-orchestrator
        # cascade refusal. Amending a plan that already has a downstream
        # <slug>_orchestrator.json is the intended use (operator-decision
        # bypass); the reconcile-sync policy for that case is wired in the
        # Phase-2 T5* tasks, not here.
    elif not stdout_mode:
        # Target-exists gate (TA). Skipped when --stdout is set since stdout
        # mode does not touch the target file.
        target = _output_target(plan_dir, slug, args.step)
        if target.exists() and not reopen:
            raise _TargetExistsNoReopenError(
                f"target artifact already exists: {target}; "
                f"pass --reopen to overwrite intentionally "
                f"(single-step --reopen does NOT auto-cascade — if a "
                f"downstream {slug}_orchestrator.json also exists, "
                f"delete it first)"
            )
        # Cascade refusal: re-running /plan with --reopen when the
        # downstream <slug>_orchestrator.json exists. Single-step --reopen
        # must not silently invalidate the orchestrator (which would
        # cascade through phase_spawn / chain driver consumers).
        if args.step == "plan" and reopen:
            orchestrator_target = plan_dir / f"{slug}_orchestrator.json"
            if orchestrator_target.exists():
                raise _TargetExistsNoReopenError(
                    f"refusing to re-run plan with --reopen because the "
                    f"downstream artifact exists: {orchestrator_target}. "
                    f"Single-step --reopen does NOT auto-cascade. Delete "
                    f"{orchestrator_target} first (and any orchestrator-"
                    f"derived state such as _state.json / "
                    f"_orchestrator_log.jsonl), then re-run "
                    f"`bin/plan --reopen {slug}`."
                )

    recon = _read_md_group(plan_dir, slug, "recon")
    qa = _read_md_group(plan_dir, slug, "qa")
    research = _read_md_group(plan_dir, slug, "research")
    qna = _read_md_group(plan_dir, slug, "qna")
    lessons = _read_lessons(plan_dir)

    prior_plan: str | None = None
    if args.step == "implplan":
        prior_plan = _read_prior_plan_json(plan_dir, slug)
        if prior_plan is None:
            raise _UsageError(
                f"implplan step requires {slug}_plan.json to exist; "
                f"run `bin/plan {slug}` first or place the file manually"
            )
    elif amend:
        # plan_surgical_amend §SC6 (T6b) — the prior-plan feed. In amend
        # mode the existing `<slug>_plan.json` is loaded into the SAME
        # `prior_plan_json` slot the implplan step uses, so Call 1 reasons
        # against the CURRENT plan when emitting a surgical patch instead of
        # seeing the `(no prior plan)` placeholder a fresh plan run uses.
        # Reuses the canonical `_read_prior_plan_json` loader (the implplan
        # reader) — no second hand-rolled reader. The inverse gate above
        # already proved the target exists, so a None here would be a
        # filesystem race rather than the missing-plan usage case; guard it
        # loudly all the same so the patch path never feeds a placeholder.
        prior_plan = _read_prior_plan_json(plan_dir, slug)
        if prior_plan is None:
            raise _UsageError(
                f"--amend requires an existing target to patch, but "
                f"{slug}_plan.json could not be read (it existed at the gate "
                f"check but is now absent — concurrent modification?)"
            )

    # Per std_command_operator_extensions TD + SC10: enforce the 8KB UTF-8
    # byte cap on `--directive` here, BEFORE the SDK call. The argparse
    # layer accepts arbitrarily long strings (no `type=` callable); this
    # post-parse validation gives a uniform stderr-envelope and exit code 1
    # path. Mirrors the qa-side cap in `bin._qa.main._build_inputs`.
    directive = getattr(args, "directive", None)
    if directive is not None:
        actual = len(directive.encode("utf-8"))
        if actual > _DIRECTIVE_MAX_BYTES:
            raise _UsageError(
                f"directive exceeds 8KB limit ({actual} bytes); "
                f"shorten or split"
            )

    return PlannerInputs(
        recon_findings=recon,
        qa_findings=qa,
        research_findings=research,
        qna_findings=qna,
        lessons_findings=lessons,
        repo_state_summary=args.repo_state,
        prior_plan_json=prior_plan,
        tier=args.tier,
        directive=directive,
    )


def _output_target(plan_dir: Path, slug: str, step: str) -> Path:
    """Resolve the target output path.

    Per cross-cutting line 236 of implplan: the canonical filenames are
    `<slug>_plan.{json,md}` and `<slug>_orchestrator.{json,md}`. Plan
    §D's "implplan" step writes `<slug>_orchestrator.json`.
    """
    if step == "plan":
        return plan_dir / f"{slug}_plan.json"
    return plan_dir / f"{slug}_orchestrator.json"


# --- amend lifecycle classification (plan_surgical_amend §SC5 / T5d) --------- #

_AMEND_LIFECYCLE_CLOSED = "closed"
_AMEND_LIFECYCLE_ACTIVE = "active"
_AMEND_LIFECYCLE_DRAFT = "draft"


def _classify_amend_target(plan_dir: Path, slug: str) -> str:
    """Classify the amend target's plan↔orchestrator lifecycle (T5d).

    Returns one of three closed strings that drive the amend dispatch's
    Phase-2 reconcile/deny branching:

      * ``"closed"`` — the orchestrator is MARKED CLOSED
        (`closed_state.is_closed`: the `_orchestrator_closed.lock` sentinel
        exists). A closed plan is sealed/frozen; T5d DENIES the amend.
      * ``"active"`` — a `<slug>_orchestrator.json` EXISTS and the plan is
        NOT closed. The amend runs the reconcile/sync path
        (`reconcile_active_plan` → if permitted, apply + `sync_reconciliation
        _to_orchestrator`).
      * ``"draft"`` — no orchestrator exists yet. The amend is a Phase-1
        draft-plan amend: apply the patch with byte-preservation, NO
        reconcile, NO sync (unchanged from Phase 1, the yaml_refactor case).

    The CLOSED check is FIRST: a closed plan is closed regardless of whether
    the orchestrator JSON is still on disk (the sentinel is the authority,
    per `closed_state.is_closed`). Then orchestrator-existence splits
    active-vs-draft. This is the single seam that decides which Phase-2
    branch the amend dispatch takes; keeping it a pure classifier (no side
    effects) keeps the dispatch's branching readable and unit-testable.
    """
    # CLOSED wins outright (sentinel is authoritative; a closed plan may still
    # have its orchestrator JSON on disk).
    if _orchestrator_is_closed(plan_dir):
        return _AMEND_LIFECYCLE_CLOSED
    # Orchestrator present + not closed → active (the reconcile/sync surface).
    orchestrator_json = plan_dir / f"{slug}_orchestrator.json"
    if orchestrator_json.exists():
        return _AMEND_LIFECYCLE_ACTIVE
    # No orchestrator → draft (Phase-1 behavior unchanged).
    return _AMEND_LIFECYCLE_DRAFT


def _write_output(target: Path, payload: dict) -> None:
    """Atomic-write the planner output JSON to `target`."""
    # Lazy import — §B's atomic_write lives in bin/_render_plan/.
    from bin._render_plan.atomic_write import write_atomic

    body = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    write_atomic(target, body)


def _render_md_twin(target: Path, render_kind: str) -> tuple[bool, str]:
    """Render the canonical MD twin for the just-written `target` JSON.

    `target` is the EXACT `<slug>_plan.json` / `<slug>_orchestrator.json` path
    the planner just wrote. We render via `--from-json <target>` (not via the
    bare `<slug>`) so the renderer operates on the precise artifact the planner
    controls instead of re-deriving the path through the render module's own
    independent `_PLANS_DIR`. That coupling-removal matters two ways: (1) the
    MD twin is guaranteed to be the twin OF the file we wrote (no slug→dir
    re-resolution drift), and (2) it keeps the render decoupled from the
    planner's `_PLANS_DIR` so a single `_PLANS_DIR` override (tests, alt roots)
    does not have to be mirrored into the render module to keep the twin in the
    same tree.

    Returns `(failed, detail)`:
      * `failed` is True iff the render RAISED an exception OR returned a
        non-zero exit code. The render module catches its own
        template/validation/IO errors and maps them to exit codes (it does
        not raise on those), so a non-zero return is the primary failure
        signal; the try/except guards an unexpected hard crash.
      * `detail` is a human-readable reason on failure (empty on success).

    plan_surgical_amend §SC6 (T6f): factored out of the inline auto-render so
    BOTH the non-amend best-effort path and the amend rollback transaction
    consume the SAME render call (one render, one failure decision). The
    render writes the MD twin through render_plan's own `write_atomic` (Python
    I/O), which by the T0 "Python-IO-bypasses-hook" contract does NOT
    re-trigger the `plan_render_on_edit` PostToolUse hook — so this renders the
    twin EXACTLY ONCE (no hook-driven double render).
    """
    try:
        from bin._render_plan.main import main as _render_main

        rc = _render_main(["--from-json", str(target), "--kind", render_kind])
    except Exception as exc:  # noqa: BLE001 — treat a hard crash as failure
        return True, f"render_plan auto-trigger crashed: {exc}"
    if rc != 0:
        return True, f"render_plan returned exit {rc}"
    return False, ""


def _rollback_json(target: Path, pre_amend_bytes: bytes) -> bool:
    """Restore `target` to its EXACT pre-amend bytes (amend rollback).

    plan_surgical_amend §SC6 (T6f): when an amend's MD-twin render fails after
    the amended JSON was written, restore the captured pre-amend bytes so the
    JSON never diverges from its (un-re-rendered) MD twin. Byte-equality is the
    invariant the rollback test asserts, so we restore the captured BYTES
    verbatim (decode as UTF-8 — JSON is UTF-8 and round-trips losslessly — and
    write through the SAME atomic writer the forward write used, so the restore
    is itself crash-safe: temp-file + rename).

    Returns True on a successful restore, False if the restore itself failed
    (the only path on which the transaction cannot fully unwind).
    """
    from bin._render_plan.atomic_write import AtomicWriteError, write_atomic

    try:
        write_atomic(target, pre_amend_bytes.decode("utf-8"))
    except (AtomicWriteError, OSError, UnicodeDecodeError):
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse already wrote to stderr; convert to our exit code.
        return exit_codes.EXIT_USAGE if exc.code != 0 else exit_codes.EXIT_OK

    try:
        plan_dir = _resolve_plan_dir(args.slug)
    except _UsageError as exc:
        _emit_stderr_json({"error": "usage", "detail": str(exc)})
        return exit_codes.EXIT_USAGE

    # Detect whether the target already exists BEFORE _build_inputs (which
    # raises on the target-exists-no-reopen path). This lets us emit the
    # "Reopened: overwrote <abs-path>" stderr notice after a successful
    # write — but ONLY when an overwrite actually occurred (i.e., the target
    # was present at start AND the run wrote it). Per TA test:
    # test_planner_reopen_stderr_notice asserts the notice fires only on
    # actual overwrite, not on a clean first-run write.
    if not getattr(args, "stdout", False):
        _pre_target = _output_target(plan_dir, args.slug, args.step)
        _target_existed_before = _pre_target.exists()
    else:
        _pre_target = None
        _target_existed_before = False

    try:
        inputs = _build_inputs(plan_dir, args.slug, args)
    except _TargetExistsNoReopenError as exc:
        _emit_stderr_json(
            {"error": "target_exists_no_reopen", "detail": str(exc)}
        )
        return exit_codes.EXIT_TARGET_EXISTS_NO_REOPEN
    except _UsageError as exc:
        _emit_stderr_json({"error": "usage", "detail": str(exc)})
        return exit_codes.EXIT_USAGE

    # plan_surgical_amend §SC6 (T6e) — the amend dispatch starts HERE, at the
    # Call-2 step selection. `--amend` is a FLAG on the `plan` subcommand, so
    # `args.step == "plan"` even in amend mode (T6d flagged this). The two-call
    # layer keys its Call-2 schema + system-prompt off the `step` ARGUMENT, so
    # we pass an EFFECTIVE step of "amend" when amending — that is what makes
    # invoke_planner emit a `plan_patch_v1` PATCH (the third schema-selection
    # branch, T6d) instead of a full plan. `args.step` ("plan") is retained for
    # the output-target resolution + render-kind below, so the amended artifact
    # still lands at `<slug>_plan.json` and renders as a plan.
    amend = getattr(args, "amend", False)
    effective_step = "amend" if amend else args.step

    try:
        result: PlannerResult = invoke_planner(
            slug=args.slug,
            step=effective_step,
            inputs=inputs,
            chain_id=args.chain_id,
        )
    except PlannerEmissionExhausted as exc:
        _emit_stderr_json(
            {
                "error": "error_max_structured_output_retries",
                "attempt_count": exc.attempt_count,
                "last_attempt_output": exc.last_attempt_output,
                "call1_reasoning_md_chars": len(exc.call1_reasoning_md),
                "detail": str(exc),
            }
        )
        # T6g — on the amend path, an SDK-exhaustion still counts as one
        # invocation: append an audit row with empty ops + result label so
        # the operator can see this --amend attempt happened (and why it
        # produced no edit). Best-effort: failures are silent.
        if amend:
            _append_amend_audit_row(
                plan_dir,
                args.slug,
                directive=getattr(args, "directive", None),
                ops=[],
                result="sdk_exhausted",
                detail=str(exc),
            )
        return exit_codes.EXIT_SDK_RETRY_EXHAUSTED

    # plan_surgical_amend §SC6 (T6e) — the amend APPLY dispatch. This is the
    # load-bearing branch that converts Call 2's emission into the artifact that
    # gets written. It MUST run BEFORE the write-raw branch below:
    #
    #   * In a normal plan/implplan run, `result.call2_emitted_json` IS the full
    #     artifact, so the write-raw branch dumps it straight to disk.
    #   * In amend mode, `result.call2_emitted_json` is a `plan_patch_v1` PATCH
    #     (a keyed op-list), NOT a plan. Writing it raw would CORRUPT plan.json.
    #     So here we APPLY the patch to the prior plan (loaded into
    #     `inputs.prior_plan_json` by T6b) via `patch_apply.apply_patch`, which
    #     returns the AMENDED plan and internally re-validates it against
    #     `plan_v1`. The downstream write/stdout/render path then operates on
    #     that amended plan — never on the raw patch.
    #
    # Exception → exit taxonomy (from T2/T3 — every loud failure maps here):
    #   PatchPostApplyInvalid → exit 43 (the patch applied cleanly but the
    #       resulting plan failed plan_v1 re-validation; the distinct
    #       post-apply-invalid halt family, NOT 8 / 16).
    #   PatchBoundExceeded    → exit 1 (usage; touched > refuse% — the message
    #       directs the operator to `--reopen`).
    #   PatchIntegrityError   → exit 1 (usage; a task remove would dangle a
    #       depends_on edge — surfaced before post-apply re-validation).
    #   PatchApplyError       → exit 1 (usage; addressing / missing-key /
    #       collision precondition failure).
    #
    # Seam note (T6e vs T6f): T6e routes the CORRECT (amended, not raw) content
    # to the EXISTING write path and propagates exit 43. The atomic-write +
    # MD-twin render + rollback-on-render-failure hardening is T6f; it is NOT
    # built here.
    payload = result.call2_emitted_json
    # T6g — capture the ordered op-list + operator directive ONCE, so every
    # amend exit path (success and the loud-failure trio) writes a single
    # accurate audit row at <slug>_amend_log.jsonl. The op-list is the patch
    # envelope's `ops` (Call 2's emission), preserved in array order — the
    # same determinism contract `patch_apply.apply_patch` honors.
    _amend_ops: list = []
    _amend_directive: str | None = getattr(args, "directive", None)
    if amend and isinstance(result.call2_emitted_json, dict):
        _amend_ops = list(result.call2_emitted_json.get("ops") or [])
    if amend:
        # The prior plan was loaded into `inputs.prior_plan_json` by the T6b
        # feed and the inverse gate (T6a) already proved the target existed, so
        # this is non-None in the normal path; guard the race defensively.
        if inputs.prior_plan_json is None:  # pragma: no cover - gate guarantees this
            _emit_stderr_json(
                {
                    "error": "usage",
                    "detail": (
                        f"--amend lost the prior plan for {args.slug} before "
                        f"apply (concurrent modification?)"
                    ),
                }
            )
            _append_amend_audit_row(
                plan_dir,
                args.slug,
                directive=_amend_directive,
                ops=_amend_ops,
                result="usage",
                detail="prior_plan_json lost between gate and apply",
            )
            return exit_codes.EXIT_USAGE

        # plan_surgical_amend §SC5 (T5d) — the Phase-2 reconcile/deny seam.
        # In Phase 1, --amend deliberately BYPASSED the cascade refusal on a
        # promoted plan (T6a) so a draft could be amended even with a
        # downstream orchestrator. T5d converts that bypass into the real
        # lifecycle-aware path, gated on the amend target's classification:
        #
        #   * CLOSED  → DENY (the gate fires HERE, BEFORE apply/reconcile/write
        #               — a closed plan is sealed; nothing is applied, written,
        #               synced, or logged beyond a clean failure-labeled audit
        #               row). The deny is loud + cites the sanctioned path. Per
        #               T5a's handoff note, the closed-plan denial is T5d's gate,
        #               NOT baked into the reconciler.
        #   * ACTIVE  → run the reconcile/sync path: reconcile_active_plan(...)
        #               decides per-op; a REFUSE surfaces cleanly (no apply, no
        #               write) and a PERMIT falls through to the T6f atomic
        #               write+render, after which sync_reconciliation_to
        #               _orchestrator(...) applies the task delta + status-
        #               preserving _state.json resync + continuity row. The
        #               ReconciliationPlan is captured here and consumed AFTER a
        #               successful write (all-or-nothing: the sync runs only if
        #               the plan.json amend durably landed).
        #   * DRAFT   → Phase-1 behavior UNCHANGED (no reconcile, no sync).
        #
        # The classification is computed ONCE; `_amend_lifecycle` /
        # `_reconciliation` are read in the write/sync sequencing below.
        _amend_lifecycle = _classify_amend_target(plan_dir, args.slug)
        _reconciliation = None  # set to a non-refusing ReconciliationPlan for ACTIVE

        if _amend_lifecycle == _AMEND_LIFECYCLE_CLOSED:
            # CLOSED plan: DENY before any apply. A closed plan is frozen — the
            # sanctioned path is to RE-OPEN the lifecycle deliberately, not to
            # surgically patch a sealed plan. Distinct stderr envelope so a
            # caller does not mistake this for the --reopen target-exists case;
            # exit 8 reuses the "precondition the operator must resolve"
            # registry family (A.impl.3a code 8) rather than minting a code SC5
            # does not call for.
            _emit_stderr_json(
                {
                    "error": "amend_closed_denied",
                    "slug": args.slug,
                    "detail": (
                        f"refusing to --amend {args.slug}: the orchestrator is "
                        f"CLOSED (sealed). A closed plan is frozen and cannot be "
                        f"surgically patched. To change a closed plan, reopen "
                        f"its lifecycle deliberately (tear down the orchestrator "
                        f"and re-derive via `bin/plan --reopen {args.slug}`) — "
                        f"--amend is for active/draft plans only."
                    ),
                }
            )
            _append_amend_audit_row(
                plan_dir,
                args.slug,
                directive=_amend_directive,
                ops=_amend_ops,
                result="closed_denied",
                detail="orchestrator marked closed; --amend denied (sealed plan)",
            )
            return exit_codes.EXIT_TARGET_EXISTS_NO_REOPEN

        prior_plan = json.loads(inputs.prior_plan_json)
        try:
            payload = apply_patch(prior_plan, result.call2_emitted_json)
        except PatchPostApplyInvalid as exc:
            _emit_stderr_json(
                {
                    "error": "amend_post_apply_invalid",
                    "violations": exc.violations,
                    "detail": str(exc),
                }
            )
            _append_amend_audit_row(
                plan_dir,
                args.slug,
                directive=_amend_directive,
                ops=_amend_ops,
                result="post_apply_invalid",
                detail=str(exc),
            )
            return exit_codes.EXIT_AMEND_POST_APPLY_INVALID
        except PatchBoundExceeded as exc:
            _emit_stderr_json(
                {
                    "error": "amend_bound_exceeded",
                    "touched": exc.touched,
                    "total": exc.total,
                    "detail": str(exc),
                }
            )
            _append_amend_audit_row(
                plan_dir,
                args.slug,
                directive=_amend_directive,
                ops=_amend_ops,
                result="bound_exceeded",
                detail=str(exc),
            )
            return exit_codes.EXIT_USAGE
        except PatchIntegrityError as exc:
            _emit_stderr_json(
                {
                    "error": "amend_integrity",
                    "removed_id": exc.removed_id,
                    "referencing_ids": exc.referencing_ids,
                    "detail": str(exc),
                }
            )
            _append_amend_audit_row(
                plan_dir,
                args.slug,
                directive=_amend_directive,
                ops=_amend_ops,
                result="integrity",
                detail=str(exc),
            )
            return exit_codes.EXIT_USAGE
        except PatchApplyError as exc:
            _emit_stderr_json(
                {
                    "error": "amend_apply",
                    "op_index": exc.op_index,
                    "op_kind": exc.op_kind,
                    "action": exc.action,
                    "detail": str(exc),
                }
            )
            _append_amend_audit_row(
                plan_dir,
                args.slug,
                directive=_amend_directive,
                ops=_amend_ops,
                result="apply_failed",
                detail=str(exc),
            )
            return exit_codes.EXIT_USAGE

        # plan_surgical_amend §SC5 (T5d) — ACTIVE-plan reconcile DECISION. The
        # patch applied cleanly to the prior plan (above), so it is structurally
        # sound; now, for an ACTIVE plan, decide whether it reconciles against
        # the LIVE orchestrator/_state.json. This is a READ-ONLY decision
        # (reconcile_active_plan never writes); the resulting ReconciliationPlan
        # is consumed by the post-write sync below. A REFUSE (task replace/remove
        # of an in-flight wip/done task) surfaces CLEANLY here — BEFORE any write
        # — so nothing is applied to plan.json, the orchestrator, or _state.json;
        # the refusal cites the wholesale path (--reopen) and is exit 1 (usage),
        # mirroring the PatchBoundExceeded / PatchIntegrityError refusal class.
        if _amend_lifecycle == _AMEND_LIFECYCLE_ACTIVE:
            try:
                _reconciliation = reconcile_active_plan(
                    result.call2_emitted_json, plan_dir, args.slug
                )
            except Exception as exc:  # noqa: BLE001 — unreadable live DAG must not silently sync
                # An unreadable live orchestrator / _state.json (loader raised)
                # must NOT silently fall through to a permissive write — it would
                # advance plan.json against an orchestrator we could not inspect.
                # Surface it loudly as a usage refusal (write NOTHING).
                _emit_stderr_json(
                    {
                        "error": "amend_reconcile_unreadable",
                        "slug": args.slug,
                        "detail": (
                            f"--amend could not read the live orchestrator/state "
                            f"for {args.slug} to reconcile the patch against the "
                            f"in-flight DAG: {exc}"
                        ),
                    }
                )
                _append_amend_audit_row(
                    plan_dir,
                    args.slug,
                    directive=_amend_directive,
                    ops=_amend_ops,
                    result="reconcile_unreadable",
                    detail=str(exc),
                )
                return exit_codes.EXIT_USAGE

            if _reconciliation.is_refused:
                # Loud, CLEAN refusal: the decision was computed before any side
                # effect, so no plan.json / orchestrator / _state.json /
                # continuity row was written. Render which in-flight task(s) and
                # why via the ReconciliationRefused message (it names the task,
                # its status, and points at --reopen).
                refusal = ReconciliationRefused(_reconciliation.refused_ops)
                _emit_stderr_json(
                    {
                        "error": "amend_reconcile_refused",
                        "slug": args.slug,
                        "refused": [
                            {
                                "op_index": op.op_index,
                                "op_kind": op.op_kind,
                                "action": op.action,
                                "target_id": op.target_id,
                                "effective_status": op.effective_status,
                                "reason": op.reason,
                            }
                            for op in _reconciliation.refused_ops
                        ],
                        "detail": str(refusal),
                    }
                )
                _append_amend_audit_row(
                    plan_dir,
                    args.slug,
                    directive=_amend_directive,
                    ops=_amend_ops,
                    result="reconcile_refused",
                    detail=str(refusal),
                )
                return exit_codes.EXIT_USAGE
            # PERMITTED (DELTA_SYNC and/or PERMIT_WITH_ADVISORY): keep
            # `_reconciliation` for the post-write sync. Surface any advisories
            # so the operator sees that an in-flight plan's prose/criteria (or a
            # not-yet-started task / new task) was amended underneath a running
            # orchestrator.
            for _adv in _reconciliation.advisories:
                print(f"Amend advisory: {_adv}", file=sys.stderr)

    # Stamp the canonical slug (and, for implplan, plan_ref) over whatever the
    # model emitted. The slug is fixed by the CLI arg / directory layout and
    # must NEVER be trusted from the LLM, which can "improve" or abbreviate it —
    # observed 2026-05-29: implplan emitted slug 'qa_subject_generalization' for
    # 'qa_review_target_generalization', leaving plan_ref dangling at a
    # nonexistent file. Stamping here (before both --stdout and the atomic
    # write) keeps the written artifact's identity fields aligned with the dir.
    # In amend mode `payload` is the AMENDED plan (apply_patch output), so the
    # stamp lands on a plan dict exactly as in a normal `plan` run — never on
    # the raw patch (which `apply_patch` already consumed).
    if isinstance(payload, dict):
        payload["slug"] = args.slug
        if args.step == "implplan":
            payload["plan_ref"] = f"{args.slug}_plan.json"

    # Emit / write the result.
    if args.stdout:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return exit_codes.EXIT_OK

    target = _output_target(plan_dir, args.slug, args.step)

    # plan_surgical_amend §SC6 (T6f) — the write+render ROLLBACK TRANSACTION,
    # scoped AMEND-ONLY. An amend is the one path where the JSON write and the
    # MD-twin render must be atomic with respect to each other: amend operates
    # on an already-authored plan that may already back a downstream
    # orchestrator, so a JSON that advances while its MD twin does not (a render
    # failure mid-amend) would leave the two artifacts DIVERGED — exactly the
    # yaml_refactor drift class. So for amend we capture the EXACT pre-amend
    # on-disk bytes BEFORE the write and, if the render then RAISES or returns a
    # non-zero exit, restore those bytes (rollback) so NEITHER artifact is left
    # changed. The inverse gate (T6a) already proved the target exists in amend
    # mode, so the prior bytes are always capturable here.
    #
    # The normal `/plan` and `/implplan` flows are DELIBERATELY left on the
    # best-effort auto-render path (a render failure warns but does not fail the
    # CLI and never rolls back the JSON) — their write is a fresh authorship /
    # wholesale --reopen, not a surgical patch of a plan that may already back an
    # orchestrator, so the divergence risk the transaction guards does not apply
    # and wrapping them would be a behavior change. `_pre_amend_bytes` is None on
    # those paths, which is the structural switch for the amend-only scope.
    _pre_amend_bytes: bytes | None = None
    if amend:
        try:
            _pre_amend_bytes = target.read_bytes()
        except OSError as exc:  # pragma: no cover - gate guarantees existence
            _emit_stderr_json(
                {
                    "error": "usage",
                    "detail": (
                        f"--amend could not read the prior {target} bytes for "
                        f"the rollback transaction (concurrent modification?): "
                        f"{exc}"
                    ),
                }
            )
            return exit_codes.EXIT_USAGE

    try:
        _write_output(target, payload)
    except Exception as exc:  # noqa: BLE001 — wrap any atomic-write failure
        _emit_stderr_json(
            {"error": "atomic_write_failed", "target": str(target), "detail": str(exc)}
        )
        return exit_codes.EXIT_ATOMIC_WRITE_FAILED

    # Per TA: surface a "Reopened: overwrote <abs-path>" stderr notice only
    # when an overwrite actually occurred (target existed pre-run AND we
    # just wrote it). A first-run write (target did NOT exist) is silent
    # on stderr so log-watchers do not see false-positive destructive-op
    # signals on normal first authorship.
    if _target_existed_before and getattr(args, "reopen", False):
        print(f"Reopened: overwrote {target.resolve()}", file=sys.stderr)

    # Also persist Call 1 reasoning beside the JSON for forensic value.
    # Per plan §D.5: Call 1 reasoning is logged verbatim.
    reasoning_target = plan_dir / f"_planner_call1_{args.step}.md"
    try:
        from bin._render_plan.atomic_write import write_atomic
        write_atomic(reasoning_target, result.call1_reasoning_md)
    except Exception:  # noqa: BLE001 — best-effort; don't fail the CLI
        pass

    # Auto-render the canonical MD twin (<slug>_plan.md / <slug>_orchestrator.md)
    # via bin/_render_plan. Was previously an operator-manual step; now fires on
    # every successful JSON write so the MD twin never drifts from the JSON.
    #
    # The render goes through Python I/O (render_plan's own `write_atomic`), NOT
    # the Edit/Write tool, so it does NOT re-trigger the `plan_render_on_edit`
    # PostToolUse hook — there is no hook-driven SECOND render (the T0
    # "Python-IO-bypasses-hook" contract; cf. bin/_hooks/plan_render_on_edit.py
    # lines 15-16). The amend write therefore renders the twin EXACTLY ONCE.
    #
    # Non-amend: best-effort — a render failure warns but does not fail the CLI
    # (JSON is source of truth; MD is a derived view). Amend: a render failure
    # triggers the rollback below.
    render_kind = "orchestrator" if args.step == "implplan" else "plan"
    render_failed, render_detail = _render_md_twin(target, render_kind)
    if render_failed:
        if _pre_amend_bytes is not None:
            # Amend-mode rollback: the JSON advanced but its MD twin did not.
            # Restore the EXACT pre-amend bytes so the transaction leaves
            # NEITHER artifact changed (byte-for-byte on the JSON; the MD twin
            # was never successfully (re)written for this amend, so it still
            # reflects the prior plan). Restore via the SAME atomic writer
            # (temp + rename) so the restore itself is crash-safe.
            #
            # Exit-code choice (NO new code is minted by T6f — exit_codes.py is
            # outside this task's scope and ALL_CODES stays {0,1,7,8,16,43}):
            # the amend persist+render is a single write transaction (the MD
            # twin is itself written via render_plan's `write_atomic`); a render
            # failure that forces the rollback is therefore surfaced as
            # EXIT_ATOMIC_WRITE_FAILED (7) — "the artifact write transaction did
            # not durably complete; it was rolled back" — rather than a false
            # EXIT_OK (the operator's amend did NOT land). The stderr envelope
            # carries the precise rolled-back / divergence-avoided semantics for
            # a caller that wants more than the numeric.
            _rolled_back = _rollback_json(target, _pre_amend_bytes)
            _emit_stderr_json(
                {
                    "error": "amend_render_failed_rolled_back",
                    "target": str(target),
                    "rolled_back": _rolled_back,
                    "detail": (
                        f"render of {args.slug}_plan.md failed after the amended "
                        f"JSON write ({render_detail}); rolled the JSON back to "
                        f"its pre-amend bytes to avoid JSON<->MD divergence"
                    ),
                }
            )
            # T6g — the rolled-back amend is still an invocation; log it.
            _append_amend_audit_row(
                plan_dir,
                args.slug,
                directive=_amend_directive,
                ops=_amend_ops,
                result="render_failed_rolled_back",
                detail=render_detail,
            )
            return exit_codes.EXIT_ATOMIC_WRITE_FAILED
        # Non-amend best-effort: warn and continue (JSON already written).
        print(
            f"warning: render_plan failed for {args.slug} --kind "
            f"{render_kind}: {render_detail}",
            file=sys.stderr,
        )

    # T6g — successful amend: emit the "Amended: <field/key>" stderr notice
    # and append one JSONL row to the per-slug audit log. Both run only on
    # the success path (a render-failure rollback is logged separately
    # above and does NOT emit the success notice — the amend did not land).
    # The stderr notice is parallel to the existing "Reopened: overwrote"
    # notice (test_planner_reopen_stderr_notice.py): a single line naming
    # the touched fields/keys in op-list order, so the operator can see at
    # a glance what was patched without `jq`-ing the log.
    if amend:
        # plan_surgical_amend §SC5 (T5d) — ACTIVE-plan SYNC, run ONLY now that
        # the plan.json amend has durably landed (write + MD-twin render both
        # succeeded). `_reconciliation` is a NON-refusing ReconciliationPlan for
        # an ACTIVE plan (a refusal already short-circuited with exit 1 BEFORE
        # any write) and None for a DRAFT plan (no orchestrator → no sync, Phase
        # 1 unchanged). The single sync call applies the orchestrator task
        # delta(s) AND the T5b status-preserving _state.json resync (survivors
        # keep their status, each new id seeds `ready`) AND the T5c append-only
        # continuity row — all under one held state flock. It enforces
        # raise_if_refused internally (a no-op here, the plan is permitted).
        if _reconciliation is not None:
            try:
                _synced_ids = sync_reconciliation_to_orchestrator(
                    _reconciliation,
                    plan_dir,
                    args.slug,
                    reason=(
                        f"amend reconcile sync for {args.slug}: "
                        f"{_format_amended_keys(_amend_ops)}"
                    ),
                )
            except Exception as exc:  # noqa: BLE001 — a partial sync must be loud
                # The plan.json amend already landed (durable write + render);
                # the post-write orchestrator/state resync then failed. Surface
                # it loudly rather than reporting a clean "Amended:" — the
                # operator must know the orchestrator side did not sync. The
                # plan.json is NOT rolled back here (it is a valid amended plan;
                # the rollback transaction guards render-divergence, not a
                # downstream-sync hiccup) but the exit is the write-transaction-
                # incomplete code so a chain caller does not treat it as a clean
                # success.
                _emit_stderr_json(
                    {
                        "error": "amend_reconcile_sync_failed",
                        "slug": args.slug,
                        "detail": (
                            f"the {args.slug}_plan.json amend landed, but the "
                            f"orchestrator/_state.json resync failed: {exc}"
                        ),
                    }
                )
                _append_amend_audit_row(
                    plan_dir,
                    args.slug,
                    directive=_amend_directive,
                    ops=_amend_ops,
                    result="reconcile_sync_failed",
                    detail=str(exc),
                )
                return exit_codes.EXIT_ATOMIC_WRITE_FAILED
            if _synced_ids:
                print(
                    f"Synced to orchestrator: seeded "
                    f"{', '.join(_synced_ids)} ready",
                    file=sys.stderr,
                )

        amended_keys = _format_amended_keys(_amend_ops)
        print(f"Amended: {amended_keys}", file=sys.stderr)
        _append_amend_audit_row(
            plan_dir,
            args.slug,
            directive=_amend_directive,
            ops=_amend_ops,
            result="applied",
        )

    return exit_codes.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
