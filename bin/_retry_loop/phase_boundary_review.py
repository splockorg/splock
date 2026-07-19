"""Runtime phase-boundary review-gate orchestrator (§F.9).

Per splock plan §F.9 + implplan §F.impl.8. This module
realizes the RUNTIME §F.9 review-gate contract that fires between
named workflow steps inside `bin/chain-overnight`:

| Boundary | Fires reviewer? |
|---|---|
| recon → plan | No |
| plan → implplan | **Yes — `run_boundary_review("plan_to_implplan")`** |
| implplan → code | **Yes — `run_boundary_review("implplan_to_code")`** |
| code → test | Yes — covered by §F.3 test-step retry loop |
| test → done | No (Ralph-gated) |

Anchor §4a.3 — load-bearing disambiguation
------------------------------------------

The §F.9 phase-boundary review gates ARE the RUNTIME contract. They
are NOT the build-time orchestrator §5 Sonnet review junctions. The
two systems share NO control-flow code:

- **Runtime (this module).** Sonnet reviewer subagent dispatched via
  the Claude Code Agent tool during `bin/chain-overnight` execution,
  given a deterministic briefing built from CLI artifacts, emits a
  structured-output rubric with one of three closed-enum
  ``terminal_shape`` values (READY / NEEDS_REVISION / HALT). The
  chain driver dispatches based on that verdict.

- **Build-time (orchestrator §5).** Separate Sonnet review framework
  used by the orchestrator agent during substrate construction. Emits
  BLOCKER / MAJOR / MINOR / NIT findings against the build itself.
  Lives entirely outside this module; this module does NOT import,
  reference, or share any code with it.

Conflating the two would defeat the structural protection.

§A integration seam
-------------------

The chain driver (§A.impl) spawns this module via the public entry
`run_boundary_review(...)` AT phase boundaries:

- Before transitioning from phase=2 (/plan) to phase=3 (/implplan):
  `run_boundary_review(plan_dir, boundary="plan_to_implplan", ...)`.
- Before transitioning from phase=3 (/implplan) to phase=4 (/code):
  `run_boundary_review(plan_dir, boundary="implplan_to_code", ...)`.

Returns ``BoundaryVerdict`` carrying:
- ``terminal_shape``: READY / NEEDS_REVISION / HALT
- ``rubric``: the structured-output dict (preserved verbatim for the
  morning-review entry on HALT)
- ``counter_exhausted``: True iff the unified counter ran out before
  a non-NEEDS_REVISION verdict landed

The chain driver acts on terminal_shape:
- READY → advance to next phase
- NEEDS_REVISION → re-spawn prior step with `prior_diagnosis = rubric`
  (NOT conversational); on counter-exhaust → defer task + write
  morning-review entry
- HALT → exit 10 (`EXIT_PHASE_BOUNDARY_HALT`); morning-review entry
  written by halt_handoff
"""

from __future__ import annotations

import enum
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from . import briefing as briefing_mod, halt_handoff, iteration_loop, rubric as rubric_mod

logger = logging.getLogger(__name__)


Boundary = Literal["plan_to_implplan", "implplan_to_code"]


# ----------------------------------------------------------------------
# Verdict + record types
# ----------------------------------------------------------------------

class TerminalShape(enum.Enum):
    """Mirrors `rubric.TERMINAL_SHAPE_VALUES` as a Python enum."""

    READY = "READY"
    NEEDS_REVISION = "NEEDS_REVISION"
    HALT = "HALT"


@dataclass
class BoundaryReviewRecord:
    """One reviewer round of forensic context."""

    iteration_n: int
    started_at: str
    ended_at: str
    rubric: dict[str, Any]
    counter_source: iteration_loop.CounterSource = "reviewer_needs_revision"


@dataclass
class BoundaryVerdict:
    """Top-level result returned by `run_boundary_review`."""

    terminal_shape: TerminalShape
    rubric: dict[str, Any]
    records: list[BoundaryReviewRecord] = field(default_factory=list)
    counter_exhausted: bool = False
    halt_entry_path: Path | None = None


# ----------------------------------------------------------------------
# Public entry: run_boundary_review
# ----------------------------------------------------------------------

def run_boundary_review(
    plan_dir: Path,
    *,
    slug: str,
    chain_id: str,
    boundary: Boundary,
    spawn_reviewer_fn: Callable[..., dict] | None = None,
    respawn_prior_step_fn: Callable[..., None] | None = None,
    max_iterations: int | None = None,
) -> BoundaryVerdict:
    """Drive the runtime §F.9 review-gate loop.

    Per §F.impl.8 terminal-shape control flow:

    1. Build deterministic briefing via `briefing.build_briefing` (NEVER
       agent-authored — anchor §4a.3 element 3).
    2. Spawn reviewer subagent; receive structured rubric.
    3. Inspect ``terminal_shape``:
       a. ``READY`` — return; chain advances.
       b. ``NEEDS_REVISION`` — re-spawn prior step with rubric as input
          (NOT conversational); increment unified counter (anchor §4a.3
          element 1); if counter not exhausted, loop to step 1; if
          exhausted, segment-defer per §F.9.4.
       c. ``HALT`` — return; chain exits 10.

    Anchor §4a.3 element 4 (segment-defer on cap-exhaust):
    On exhaust the function writes a morning-review entry with the
    FULL structured context — last verdict, all prior verdicts,
    briefing — via `halt_handoff.write_halt_entry` and returns with
    ``counter_exhausted=True``.

    Parameters
    ----------
    spawn_reviewer_fn : callable | None
        Real impl: Claude Code Agent tool dispatch. Tests DI a
        recorded-response fixture.
    respawn_prior_step_fn : callable | None
        Real impl: chain driver re-spawns `/plan` or `/implplan`
        subprocess with `prior_diagnosis` env var staged. Tests DI a
        no-op closure.
    max_iterations : int | None
        Overrides the unified-counter cap. Default reads
        `iteration_loop.unified_counter_active_cap()` (3 default / 6
        overnight).
    """
    if spawn_reviewer_fn is None:
        spawn_reviewer_fn = iteration_loop._default_spawn_reviewer
    if respawn_prior_step_fn is None:
        respawn_prior_step_fn = _default_respawn_prior_step

    cap = (
        max_iterations
        if max_iterations is not None
        else iteration_loop.unified_counter_active_cap()
    )

    records: list[BoundaryReviewRecord] = []
    last_rubric: dict[str, Any] = {}
    iteration_n = 1

    while True:
        started_at = _now_iso_z()
        remaining = iteration_loop.unified_counter_get_remaining(
            plan_dir, task_id=boundary, cap=cap,
        )
        if remaining < 1:
            # Counter pre-exhausted (Ralph or test-step already drained,
            # or a prior boundary run exhausted the cap).
            if not records:
                # This run did NO work — a re-invocation after a
                # cap-exhaustion halt. Field defect (2026-07-19): this
                # used to exit silently AND append a second, EMPTY halt
                # entry ("Total iterations this halt: 0") to
                # morning-review — indistinguishable from a transport
                # failure, and the empty entry buried the real one.
                # Diagnose loudly, write nothing.
                print(
                    f"boundary review pre-exhausted: retry counter "
                    f"{cap}/{cap} for {boundary} (see "
                    f"docs/plans/{slug}/morning-review/ for the original "
                    f"halt); reset via `bin/verify boundary {slug} "
                    f"--boundary {boundary} --fresh --chain-id <id>` or "
                    f"chain resume",
                    file=sys.stderr,
                )
                return BoundaryVerdict(
                    terminal_shape=TerminalShape.HALT,
                    rubric=last_rubric,
                    records=records,
                    counter_exhausted=True,
                    halt_entry_path=None,
                )
            entry_path = halt_handoff.write_halt_entry(
                plan_dir,
                slug=slug,
                chain_id=chain_id,
                halt_reason="phase_boundary_review_exhausted",
                iteration_records=[
                    _to_iteration_record(r) for r in records
                ],
                boundary=boundary,
                briefing=_briefing_metadata(plan_dir, slug, boundary),
            )
            return BoundaryVerdict(
                terminal_shape=TerminalShape.HALT,
                rubric=last_rubric,
                records=records,
                counter_exhausted=True,
                halt_entry_path=entry_path,
            )

        # Step 1: deterministic briefing.
        prompt = briefing_mod.build_briefing(
            slug=slug,
            iteration_n=iteration_n,
            rubric_kind=boundary,
            plan_dir=plan_dir,
            prior_diagnosis=last_rubric or None,
            debug_echo=os.environ.get("OVERNIGHT_DEBUG_RETRY_PROMPT") == "1",
        )

        # Step 2: spawn reviewer subagent.
        rubric_payload = spawn_reviewer_fn(
            plan_dir=plan_dir,
            prompt=prompt,
            rubric_kind=boundary,
        )

        # Schema-version refusal per §F.impl.5 forward-compat.
        version = rubric_payload.get("rubric_version", 1)
        if not rubric_mod.is_supported_version(boundary, version):
            raise rubric_mod.UnsupportedRubricVersionError(
                kind=boundary,
                version=version,
                supported=[1],
            )

        # NOTE (post-§F mid-section F-06 fix): unified-counter
        # increment moved BELOW the terminal_shape inspection per spec
        # §F.impl.8 "cleared boundary doesn't count" — READY verdicts
        # MUST NOT consume a retry budget unit. The increment now fires
        # only on the NEEDS_REVISION branch (after the READY/HALT
        # returns at lines ~251-277).

        record = BoundaryReviewRecord(
            iteration_n=iteration_n,
            started_at=started_at,
            ended_at=_now_iso_z(),
            rubric=rubric_payload,
        )
        records.append(record)
        last_rubric = rubric_payload

        # Step 3: inspect terminal_shape.
        shape_str = rubric_mod.terminal_shape_of(rubric_payload)
        shape = TerminalShape(shape_str)

        if shape == TerminalShape.READY:
            return BoundaryVerdict(
                terminal_shape=shape,
                rubric=rubric_payload,
                records=records,
                counter_exhausted=False,
            )

        if shape == TerminalShape.HALT:
            entry_path = halt_handoff.write_halt_entry(
                plan_dir,
                slug=slug,
                chain_id=chain_id,
                halt_reason="phase_boundary_review_exhausted",
                iteration_records=[
                    _to_iteration_record(r) for r in records
                ],
                boundary=boundary,
                briefing=_briefing_metadata(plan_dir, slug, boundary),
            )
            return BoundaryVerdict(
                terminal_shape=shape,
                rubric=rubric_payload,
                records=records,
                counter_exhausted=False,
                halt_entry_path=entry_path,
            )

        # NEEDS_REVISION — re-spawn prior step with structured findings.
        # NOT conversational. The respawn function consumes the rubric
        # as appended user-prompt input.
        #
        # Anchor §4a.3 element 1 + spec §F.impl.8 "cleared boundary
        # doesn't count": bump the unified counter ONLY here (the
        # NEEDS_REVISION path). READY returned above without bump;
        # HALT returned above without bump (halt records the verdict
        # via write_halt_entry; counter mutation would be vestigial
        # because the chain driver halts before observing it).
        iteration_loop.unified_counter_increment(
            plan_dir,
            task_id=boundary,
            source="reviewer_needs_revision",
        )

        respawn_prior_step_fn(
            plan_dir=plan_dir,
            slug=slug,
            chain_id=chain_id,
            boundary=boundary,
            prior_diagnosis=rubric_payload,
        )

        iteration_n += 1
        if iteration_n > cap:
            # Cap exhausted — segment-defer per §F.9.4.
            entry_path = halt_handoff.write_halt_entry(
                plan_dir,
                slug=slug,
                chain_id=chain_id,
                halt_reason="phase_boundary_review_exhausted",
                iteration_records=[
                    _to_iteration_record(r) for r in records
                ],
                boundary=boundary,
                briefing=_briefing_metadata(plan_dir, slug, boundary),
            )
            return BoundaryVerdict(
                terminal_shape=TerminalShape.HALT,
                rubric=last_rubric,
                records=records,
                counter_exhausted=True,
                halt_entry_path=entry_path,
            )


# ----------------------------------------------------------------------
# Default impls (overridable for tests)
# ----------------------------------------------------------------------

def _default_respawn_prior_step(
    *,
    plan_dir: Path,
    slug: str,
    chain_id: str,
    boundary: Boundary,
    prior_diagnosis: dict[str, Any],
) -> None:
    """Real-impl placeholder for prior-step re-spawn.

    The chain driver implements this via subprocess invocation of
    `bin/_planner/main.py` (for plan_to_implplan: re-run /plan;
    for implplan_to_code: re-run /implplan) with `prior_diagnosis`
    staged as appended user-prompt input via env var.

    Tests DI a closure to avoid the subprocess spawn. The stub here
    logs and returns — production paths always inject.
    """
    logger.info(
        "default_respawn_prior_step stub — chain driver should DI a real "
        "respawner (boundary=%s slug=%s chain_id=%s)",
        boundary, slug, chain_id,
    )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _briefing_metadata(plan_dir: Path, slug: str, boundary: Boundary) -> dict[str, Any]:
    """Snapshot of briefing inputs preserved in the morning-review entry.

    Per F.9.5 item 4: morning-review entry includes briefing inputs so
    the operator can replay the reviewer's view.
    """
    return {
        "boundary": boundary,
        "slug": slug,
        "plan_summary": briefing_mod._read_plan_summary(plan_dir, slug),
        "orchestrator_shape": briefing_mod._read_orchestrator_shape(plan_dir, slug),
        "planner_telemetry": briefing_mod._read_planner_telemetry(plan_dir),
    }


def _to_iteration_record(
    record: BoundaryReviewRecord,
) -> iteration_loop.IterationRecord:
    """Adapt a boundary record into the iteration_loop record shape so
    `halt_handoff.write_halt_entry` can render uniformly.
    """
    return iteration_loop.IterationRecord(
        iteration_n=record.iteration_n,
        started_at=record.started_at,
        ended_at=record.ended_at,
        test_runner_exit_code=-1,  # N/A at boundary review
        failing_tests=[],
        diff_excerpt="(boundary review — no diff in scope)",
        rubric=record.rubric,
    )


def _now_iso_z() -> str:
    import datetime

    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


__all__ = [
    "Boundary",
    "BoundaryReviewRecord",
    "BoundaryVerdict",
    "TerminalShape",
    "run_boundary_review",
]
