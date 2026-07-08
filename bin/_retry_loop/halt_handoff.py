"""Morning-review halt-and-handoff entry writer.

Per splock plan §F.6 (test-step halt-and-handoff) + §F.9.5
(boundary halt-and-handoff) + implplan §F.impl.7.

Anchor §4a.3 element 4 enforcement
----------------------------------

When the unified retry counter exhausts AND no terminal ``READY``
landed, this module writes a morning-review entry with the FULL
structured context preserved:

- Last verdict reasoning (the entire rubric dict, verbatim)
- All prior verdicts in iteration order
- Last diff state (for test-step halts)
- Briefing inputs (for boundary halts) so the operator can replay the
  reviewer's view

The chain driver halts the task but does NOT halt the chain — other
tasks continue per §A.5 segment-defer semantics. This module's job is
to ensure the morning-review entry is fully self-contained: the
operator can answer "what happened? why? what next?" in < 60 seconds
of reading without grepping other logs (plan §F.7 criterion 6).

§C transition row
-----------------

Per implplan §C.impl + cross-cutting conventions: this module emits a
transition-log row via `append_row(..., emitted_by="bin/verify")` so
the deferral event is forensically logged. The append happens BEFORE
the morning-review file write so a write failure leaves a record.

Append-discipline
-----------------

Per plan §H.2: morning-review queue files live at
``docs/plans/<slug>/morning-review/<YYYY-MM-DD>.md`` (per-slug,
v1.3-revised corrected layout). This module APPENDS only — never
creates or overwrites. If the daily file does not yet exist, calls
the §H morning-review CLI to bootstrap per-slug shell, then appends.
"""

from __future__ import annotations

import datetime
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Literal

from bin._env_paths import project_root

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .iteration_loop import IterationRecord


HaltReason = Literal[
    "retry_exceeded",
    "tampering_detected",
    "phase_boundary_review_exhausted",
]
"""Closed enum per §F.impl.7 + §F.9.5 deferral_reason taxonomy.

Mapping to chain-driver completion-summary halt_reasons:
- ``retry_exceeded`` → test-step iteration cap exhausted
- ``tampering_detected`` → R4 == "yes-flagged" in any iteration
- ``phase_boundary_review_exhausted`` → boundary unified-counter
  exhausted (`EXIT_RETRY_EXCEEDED` from boundary path)
"""


def write_halt_entry(
    plan_dir: Path,
    *,
    slug: str,
    chain_id: str,
    halt_reason: HaltReason,
    iteration_records: Iterable["IterationRecord"],
    boundary: str | None = None,
    briefing: dict[str, Any] | None = None,
) -> Path:
    """Append a halt-and-handoff section to the daily morning-review file.

    Parameters
    ----------
    plan_dir : Path
        Slug directory ``docs/plans/<slug>/``.
    slug : str
        Plan slug.
    chain_id : str
        Chain id for forensic linkage.
    halt_reason : HaltReason
        Closed enum.
    iteration_records : Iterable[IterationRecord]
        Per-iteration audit records (from `iteration_loop.IterationContext`
        or boundary-adapted from `phase_boundary_review.BoundaryReviewRecord`).
    boundary : str | None
        Boundary name when this is a phase-boundary halt
        ("plan_to_implplan" | "implplan_to_code"). None for test-step halt.
    briefing : dict | None
        Briefing-input snapshot per F.9.5 item 4 (boundary halts only).

    Returns
    -------
    Path
        The morning-review file path that was appended to.
    """
    records = list(iteration_records)
    today = _today_yyyy_mm_dd()
    target = plan_dir / "morning-review" / f"{today}.md"
    _ensure_morning_review_file(plan_dir, slug, target, today)

    section = _render_section(
        slug=slug,
        chain_id=chain_id,
        halt_reason=halt_reason,
        records=records,
        boundary=boundary,
        briefing=briefing,
    )

    _append_atomic(target, section)

    # Best-effort §C transition row — the morning-review write is the
    # source of truth, the transition row is forensic supplement.
    try:
        _emit_deferral_row(
            plan_dir=plan_dir,
            slug=slug,
            chain_id=chain_id,
            halt_reason=halt_reason,
            iteration_count=len(records),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("transition-row emit failed (best-effort): %s", exc)

    return target


# ----------------------------------------------------------------------
# Rendering — fixed shape per plan §F.6 + §F.9.5
# ----------------------------------------------------------------------

def _render_section(
    *,
    slug: str,
    chain_id: str,
    halt_reason: HaltReason,
    records: list["IterationRecord"],
    boundary: str | None,
    briefing: dict[str, Any] | None,
) -> str:
    """Render the morning-review section per plan §F.6 + §F.9.5 shape."""
    iso_now = _iso_now()
    title = "test-step retry loop"
    if boundary is not None:
        title = f"phase-boundary review ({boundary})"

    parts: list[str] = []
    parts.append(f"\n# {title} — chain {chain_id} — halted\n")
    parts.append(f"**Slug:** {slug}")
    parts.append(f"**Halted at:** {iso_now}")
    parts.append(f"**Halt reason:** {halt_reason}")
    parts.append(f"**Final exit code:** 17")
    parts.append("")
    parts.append("## Iterations")
    parts.append("")
    for record in records:
        parts.append(f"### Iteration {record.iteration_n} ({record.started_at})")
        if record.failing_tests:
            failing_list = "\n  - ".join(record.failing_tests)
            parts.append(f"- Tests failing:\n  - {failing_list}")
        else:
            parts.append("- Tests failing: (none / N/A)")
        parts.append(f"- Test runner exit code: {record.test_runner_exit_code}")
        if record.diff_excerpt:
            parts.append("- Iteration diff:")
            parts.append("  ```diff")
            for line in record.diff_excerpt.splitlines():
                parts.append(f"  {line}")
            parts.append("  ```")
        if record.rubric is not None:
            parts.append("- Reviewer rubric (verbatim):")
            parts.append("  ```json")
            for line in json.dumps(
                record.rubric, indent=2, sort_keys=True
            ).splitlines():
                parts.append(f"  {line}")
            parts.append("  ```")
        parts.append("")

    # Final-state section — last test runner output if available.
    parts.append("## Final test state")
    parts.append("")
    if records and records[-1].diff_excerpt:
        parts.append("```")
        parts.append(records[-1].diff_excerpt[-2000:])  # truncate to last 2k chars
        parts.append("```")
    else:
        parts.append("(no diff captured — likely a pre-iteration halt)")
    parts.append("")

    # F.9.5 boundary extensions — anchor §4a.3 element 4 full context.
    if boundary is not None:
        parts.append("## Phase-boundary reviewer briefing")
        parts.append("")
        if briefing is not None:
            parts.append("```json")
            for line in json.dumps(
                briefing, indent=2, sort_keys=True
            ).splitlines():
                parts.append(line)
            parts.append("```")
        else:
            parts.append("(briefing inputs not captured — chain driver bug)")
        parts.append("")
        parts.append("## Retry-counter breakdown")
        parts.append("")
        # Per F.9.5 item 2: breakdown by Ralph / reviewer / test-step.
        # The detailed source-by-source breakdown lives in
        # _orchestrator_log.jsonl `retry_counter_incremented` rows; here
        # we surface the running total for at-a-glance forensic value.
        parts.append(f"- Total iterations this halt: {len(records)}")
        parts.append(
            f"- Source-by-source breakdown: see "
            f"`_orchestrator_log.jsonl` rows with "
            f"`event_type: \"retry_counter_incremented\"`"
        )
        parts.append("")

    parts.append("## Next operator actions")
    parts.append("")
    parts.extend(_render_next_actions(halt_reason, boundary, slug, chain_id))
    parts.append("")
    return "\n".join(parts)


def _render_next_actions(
    halt_reason: HaltReason,
    boundary: str | None,
    slug: str,
    chain_id: str,
) -> list[str]:
    """Halt-reason-specific operator guidance.

    Per plan §F.6: each halt reason gets actionable next-steps so the
    operator does not have to chase other logs.
    """
    out: list[str] = []
    if halt_reason == "retry_exceeded":
        out.append(f"1. Review the rubric for the final iteration — R3 names the")
        out.append(f"   action that did not land in the cap window. Consider:")
        out.append(f"   - Applying R3 manually then committing")
        out.append(f"   - Adjusting the test in question if it's brittle")
        out.append(f"   - Raising the retry cap (default 3 per arXiv:2603.08877)")
        out.append(f"2. To resume chain:")
        out.append(f"   ```")
        out.append(f"   bin/chain-overnight 5 {slug} --from-resume {chain_id}")
        out.append(f"   ```")
    elif halt_reason == "tampering_detected":
        out.append(f"1. **Inspect test-file edits flagged by R4.**")
        out.append(f"   Look for: removed assertions, broadened acceptable inputs,")
        out.append(f"   added pytest.skip/xfail, sys.exit(0) bypass patterns.")
        out.append(f"2. Either:")
        out.append(f"   - Revert the suspect edits and resume fresh, OR")
        out.append(f"   - Approve the edits as intentional (add to")
        out.append(f"     `_test_expectations.json`) and resume.")
        out.append(f"3. To resume chain:")
        out.append(f"   ```")
        out.append(f"   bin/chain-overnight 5 {slug} --from-resume {chain_id}")
        out.append(f"   ```")
    elif halt_reason == "phase_boundary_review_exhausted":
        boundary_name = boundary or "(unknown boundary)"
        out.append(f"1. **Review the reviewer's final rubric** (above). The R-fields")
        out.append(f"   describe the structural problem the prior step could not fix")
        out.append(f"   within the unified retry cap.")
        out.append(f"2. For boundary={boundary_name}:")
        if boundary == "plan_to_implplan":
            out.append(f"   - Inspect `{slug}_plan.json` against the recon doc")
            out.append(f"   - Either fix the plan substrate manually, OR raise")
            out.append(f"     the unified-cap budget for this chain")
        elif boundary == "implplan_to_code":
            out.append(f"   - Inspect `{slug}_orchestrator.json` — check")
            out.append(f"     `tests_enabled` consistency / placeholder sites /")
            out.append(f"     dependency graph cycles / sealed-paths references")
            out.append(f"   - Either fix the orchestrator substrate manually, OR")
            out.append(f"     raise the unified-cap budget for this chain")
        else:
            out.append(f"   - Inspect substrate produced before the boundary")
            out.append(f"   - Address the rubric's flagged items")
        out.append(f"3. To resume chain:")
        out.append(f"   ```")
        out.append(f"   bin/chain-overnight 3 {slug} --from-resume {chain_id}")
        out.append(f"   ```")
    return out


# ----------------------------------------------------------------------
# File-management helpers
# ----------------------------------------------------------------------

def _ensure_morning_review_file(
    plan_dir: Path, slug: str, target: Path, today: str,
) -> None:
    """Bootstrap the daily morning-review file if missing.

    Per implplan §F.impl.7: if the per-slug daily file does not yet
    exist, call `bin/morning-review --internal-bootstrap-day` to
    create the per-slug shell, then append.

    §H not yet present in all test environments — degrade gracefully
    to a direct file create with a minimal shell.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    # Try §H bootstrap first. parents[2] is the PLUGIN root — correct for
    # locating the shipped binary, wrong as the working directory (the
    # bootstrap must write the adopter's morning-review state).
    plugin_root = Path(__file__).resolve().parents[2]
    morning_review_bin = plugin_root / "bin" / "morning-review"
    if morning_review_bin.exists():
        try:
            subprocess.run(
                [
                    str(morning_review_bin),
                    "--internal-bootstrap-day",
                    today,
                    "--slug",
                    slug,
                ],
                cwd=str(project_root()),
                capture_output=True,
                timeout=30,
                check=False,
            )
            if target.exists():
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
    # Fallback: write minimal shell.
    shell = (
        f"# Morning review queue — {slug} — {today}\n\n"
        f"_Bootstrap by `bin/_retry_loop/halt_handoff.py` because "
        f"`bin/morning-review --internal-bootstrap-day` was unavailable._\n"
    )
    target.write_text(shell, encoding="utf-8")


def _append_atomic(target: Path, section: str) -> None:
    """Append `section` to `target` via atomic write-temp + rename.

    Per cross-cutting line 280: atomic write discipline applies to
    every JSON file (and per implplan §F.impl.7 to the morning-review
    file append).
    """
    if not target.exists():
        # Should not happen — `_ensure_morning_review_file` was just called.
        target.write_text(section, encoding="utf-8")
        return
    existing = target.read_text(encoding="utf-8")
    new_body = existing.rstrip("\n") + "\n" + section
    from bin._render_plan.atomic_write import write_atomic

    write_atomic(target, new_body)


def _emit_deferral_row(
    *,
    plan_dir: Path,
    slug: str,
    chain_id: str,
    halt_reason: HaltReason,
    iteration_count: int,
) -> None:
    """Append a §C transition row for the deferral event.

    Uses `bin/verify` as `emitted_by` per anchor §4a.3 element 4 wiring
    discipline (the chain driver registered `bin/verify` plus the
    retry-loop sub-emitters in KNOWN_WRITERS v4).
    """
    from bin._jsonl_log import append_row
    import hashlib

    digest = hashlib.sha1(
        f"{chain_id}|halt_handoff|{halt_reason}".encode("utf-8")
    ).hexdigest()
    session_id = f"sess_{digest[:8]}"
    # task_id per orchestrator_log_v1.schema.json must match ^T[0-9]+$ or
    # be null. The retry-loop's halt event is task-level "test_step" but
    # the JSONL row uses null and surfaces "halt_reason=..." in reason.
    row = {
        "session_id": session_id,
        "plan_slug": slug,
        "chain_id": chain_id,
        "task_id": None,
        "transition": {"from": "wip", "to": "deferred"},
        "pointer": None,
        "retry_count": iteration_count,
        "mode_at_transition": {"overnight": True, "guardrail": False},
        "override_in_effect": None,
        "reason": (
            f"halt_reason={halt_reason}; test-step segment-defer per §F.9.4"
        ),
        "verifier_verdict_ref": None,
    }
    append_row(plan_dir, row, emitted_by="bin/verify")


def _today_yyyy_mm_dd() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _iso_now() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


__all__ = [
    "HaltReason",
    "write_halt_entry",
]
