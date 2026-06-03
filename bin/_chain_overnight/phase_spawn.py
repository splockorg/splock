"""Per-phase spawn — dispatches the two-call planner OR the in-process retry loop.

Per implplan §A.impl + plan §A.5 SPAWNING_PHASE state. Each phase
spawn:

1. Re-reads the sentinel (defense-in-depth per A.impl.4 STARTING→RUNNING
   foreign-sentinel detect — exit 21 on mismatch).
2. Pre-check wall-clock cap per A.impl.5 (cost-cap arm retired in
   `delete_usage_caps`).
3. Dispatch:
    - phase=2 (`/plan`): in-process `invoke_planner(step="plan")` via §D.
    - phase=3 (`/implplan`): in-process `invoke_planner(step="implplan")`.
    - phase=4 (`/code`): in-process `run_test_step_loop` via §F with
      the T1-T6 SDK spawners injected as DI'd callables.
    - phase=5 (`/test`): same in-process surface as phase 4.
4. After dispatch — write JSON via §B's `write_atomic` (planner phases
   only; code/test phases don't emit substrate).
5. Verify via subprocess to `bin/verify_plan --strict <slug>` (planner
   phases only).
6. Stamp manifest exit-time fields via `manifest.stamp_phase_exit`.

§D integration:
- Maps `PlannerEmissionExhausted` → exit 16 (`EXIT_VERIFY_PLAN_REJECTED`).

§B integration:
- `invoke_planner` returns the dict; THIS module writes via
  `atomic_write.write_atomic` per plan §D.6 criterion 5 (driver-writes-
  not-subagent).
- Verifies via `subprocess.run(["python", "-m", "bin._render_plan.verify",
  "--strict", "<slug>"])`.

§F integration (phase 4 + 5) — verifier_sdk_wiring §T8 (2026-05-23):
- `run_test_step_loop` called in-process with SDK spawners DI'd by
  IDENTITY (NOT wrapped). The earlier subprocess-based shape
  (`subprocess.Popen(["python", "-m", "bin._retry_loop.main", ...])`)
  is gone — Option A (in-process, not subprocess-per-iteration) lands
  here. Exit-code mapping done via Python control flow instead of
  parsing a child process's exit code:
    - PASSED → exit 0
    - HALT_TAMPERING → exit 17 (EXIT_RETRY_EXCEEDED)
    - HALT_CAP_EXHAUSTED → exit 17 (EXIT_RETRY_EXCEEDED)
    - ReviewerEmissionExhausted (caught) → exit 16
    - smoke_check_sdk_available False → exit 16 (short-circuit)
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from . import cap_enforcement, manifest as manifest_mod, sentinel, exit_codes

logger = logging.getLogger(__name__)


PhaseCommand = Literal["/plan", "/implplan", "/code", "/test"]

PHASE_COMMAND_MAP: dict[int, PhaseCommand] = {
    2: "/plan",
    3: "/implplan",
    4: "/code",
    5: "/test",
}


@dataclass
class PhaseResult:
    """Result of a single phase spawn.

    `verdict` enum (closed):
    - `passed` — phase completed; chain proceeds.
    - `cost_cap_exceeded` — pre-spawn cost-cap halt (exit 12).
    - `wall_clock_exceeded` — pre-spawn wall-clock halt (exit 11).
    - `concurrent_chain_refused` — foreign sentinel mid-chain (exit 21).
    - `verify_plan_rejected` — §B verify failed OR §D retry exhausted (exit 16).
    - `phase_boundary_halt` — generic halt (exit 10).
    - `sealed_path_refused` — pre-stage scan refused (exit 9).
    - `insufficient_budget` — §D budget floor refused (exit 5).
    - `atomic_write_failed` — §B atomic_write failed (exit 7).
    """

    verdict: str
    exit_code: int
    phase: int
    session_id: str
    started_at: str
    ended_at: str
    cost_usd: float
    model_id: str
    halt_reason: str | None = None
    downstream_exit_code: int | None = None


def _now_iso_z() -> str:
    import datetime
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _resolve_session_id(chain_id: str, phase: int) -> str:
    """Synthesize a `sess_<8hex>` id from chain_id + phase.

    Matches `^sess_[0-9a-f]{8}$` per `orchestrator_log_v1.schema.json`
    SessionId regex.
    """
    import hashlib

    digest = hashlib.sha1(f"{chain_id}|{phase}".encode("utf-8")).hexdigest()
    return f"sess_{digest[:8]}"


def precheck_caps(
    plan_dir: Path,
    *,
    chain_id: str,
    phase: int,
    chain_started_at_epoch: float,
) -> cap_enforcement.CapVerdict:
    """Run wall-clock-cap check BEFORE spawn.

    Cost-cap arm retired in `delete_usage_caps` (2026-05-23).

    CCOR.1 (per R-cap-injection): reads the paused-time accumulator from
    `manifest.read_paused_time_accumulator` and threads
    `(total_paused_seconds, paused_since_epoch)` into
    `check_wall_clock_cap`. The accumulator stays at `(0.0, None)` on
    chains that have never paused, preserving pre-CCOR.1 behavior. When
    `paused_since` is non-None (active pause), the ISO-Z string is
    converted to a POSIX epoch using the same helper the manifest itself
    uses (`manifest._iso_z_to_epoch`) — caller-side parse keeps the
    cap-check pure (no I/O inside it).

    Returns the failing verdict, or `"pass"` if wall-clock clears.
    """
    # Read paused-time accumulator. Defensive defaults if the manifest
    # is missing or the chain_id filter doesn't match (per
    # read_paused_time_accumulator contract: returns (0.0, None) on
    # mismatch, which preserves pre-CCOR.1 behavior — never freeze on
    # the wrong chain's pause history).
    total_paused_seconds, paused_since_iso = (
        manifest_mod.read_paused_time_accumulator(plan_dir, chain_id=chain_id)
    )
    paused_since_epoch: float | None = None
    if paused_since_iso is not None:
        # Same ISO-Z → epoch conversion the manifest uses internally.
        # Keep this caller-side so `check_wall_clock_cap` stays a pure
        # function over float epochs.
        paused_since_epoch = manifest_mod._iso_z_to_epoch(paused_since_iso)

    wall = cap_enforcement.check_wall_clock_cap(
        chain_started_at_epoch=chain_started_at_epoch,
        wall_clock_cap_seconds=cap_enforcement.resolve_wall_clock_cap(),
        total_paused_seconds=total_paused_seconds,
        paused_since_epoch=paused_since_epoch,
    )
    if wall.verdict != "pass":
        return wall
    return cap_enforcement.CapVerdict(verdict="pass")


def recheck_foreign_sentinel(plan_dir: Path, expected_chain_id: str) -> bool:
    """Defense-in-depth — re-read sentinel; foreign means trouble.

    Returns True if the sentinel still matches our chain_id (safe to
    proceed). False if the sentinel is missing or belongs to another
    chain (halt with exit 21 per A.impl.4).
    """
    payload = sentinel.read_sentinel(plan_dir)
    if payload is None:
        return False
    return payload.get("chain_id") == expected_chain_id


def spawn_planner_phase(
    plan_dir: Path,
    *,
    slug: str,
    chain_id: str,
    phase: int,
    invoke_planner_fn: Any = None,
    write_atomic_fn: Any = None,
    verify_plan_fn: Any = None,
    pricing_fn: Any = None,
    inject_text: str | None = None,
) -> PhaseResult:
    """Dispatch a planner phase (phase=2 /plan; phase=3 /implplan).

    Per implplan §A.impl + §D integration:

    1. Call §D's `invoke_planner(slug, step, inputs, ...)`.
    2. On `PlannerBudgetRefused` → return PhaseResult with exit 5.
    3. On `PlannerEmissionExhausted` → return PhaseResult with exit 16.
    4. On success → write the dict via §B's `atomic_write.write_atomic`.
    5. Verify via §B's `bin/verify_plan --strict` (subprocess).
    6. Return PhaseResult with cost from `PlannerResult.call1_cost_usd
       + .call2_cost_usd`.

    Dependency-injected helpers (`invoke_planner_fn`, `write_atomic_fn`,
    `verify_plan_fn`) default to the real surfaces but allow tests to
    substitute mocks without monkey-patching imports.
    """
    if phase not in (2, 3):
        raise ValueError(f"spawn_planner_phase requires phase in (2, 3); got {phase}")
    step: Literal["plan", "implplan"] = "plan" if phase == 2 else "implplan"
    session_id = _resolve_session_id(chain_id, phase)
    started_at = _now_iso_z()

    # Lazy default-imports
    if invoke_planner_fn is None:
        from bin._planner import invoke_planner as _invoke
        invoke_planner_fn = _invoke
    if write_atomic_fn is None:
        from bin._render_plan.atomic_write import write_atomic as _wa
        write_atomic_fn = _wa
    if verify_plan_fn is None:
        verify_plan_fn = _default_verify_plan

    # Read inputs from per-slug MD files (mirrors bin/_planner/main.py
    # in-process semantics).
    inputs = _build_planner_inputs(plan_dir, slug, step)

    # Per R-inject-wiring + R-no-subagent-prompt-edits (CCOR.1): the
    # operator-supplied framed inject body is threaded into the prompt
    # at the spawn-prompt-build layer. We prepend it to the
    # `lessons_findings` field — `lessons_findings` is already
    # wrap-delim-protected (`<lessons-findings>` per userguide §3.3),
    # and the inject body carries its own
    # `<operator-inject>...</operator-inject>` framing so the spawned
    # planner sees a clearly-scoped operator-origin block.
    if inject_text is not None:
        existing = inputs.lessons_findings or ""
        merged = inject_text + ("\n\n" + existing if existing else "")
        inputs = dataclasses.replace(inputs, lessons_findings=merged)

    # Dispatch §D's two-call planner. Budget kwargs retired in
    # `delete_usage_caps` (2026-05-23) along with the planner-side
    # `PlannerBudgetRefused` arm.
    try:
        result = invoke_planner_fn(
            slug=slug,
            step=step,
            inputs=inputs,
            chain_id=chain_id,
        )
    except Exception as exc:  # noqa: BLE001 — map known exception types
        return _map_planner_exception(
            exc,
            chain_id=chain_id,
            phase=phase,
            session_id=session_id,
            started_at=started_at,
        )

    # Write the emitted JSON via §B's atomic_write.
    target = _output_target(plan_dir, slug, step)
    try:
        body = json.dumps(result.call2_emitted_json, indent=2, sort_keys=True) + "\n"
        write_atomic_fn(target, body)
    except Exception as exc:  # noqa: BLE001
        logger.warning("atomic_write failed for %s: %s", target, exc)
        return PhaseResult(
            verdict="atomic_write_failed",
            exit_code=exit_codes.EXIT_ATOMIC_WRITE_FAILED,
            phase=phase,
            session_id=session_id,
            started_at=started_at,
            ended_at=_now_iso_z(),
            cost_usd=result.call1_cost_usd + result.call2_cost_usd,
            model_id=result.call2_model_id or result.call1_model_id,
            halt_reason="atomic_write_failed",
        )

    # Verify via §B's bin/verify_plan --strict.
    verify_exit = verify_plan_fn(slug, kind=step)
    if verify_exit != 0:
        logger.warning("bin/verify_plan --strict %s exit=%s", slug, verify_exit)
        return PhaseResult(
            verdict="verify_plan_rejected",
            exit_code=exit_codes.EXIT_VERIFY_PLAN_REJECTED,
            phase=phase,
            session_id=session_id,
            started_at=started_at,
            ended_at=_now_iso_z(),
            cost_usd=result.call1_cost_usd + result.call2_cost_usd,
            model_id=result.call2_model_id or result.call1_model_id,
            halt_reason="verify_plan_rejected",
            downstream_exit_code=verify_exit,
        )

    total_cost = result.call1_cost_usd + result.call2_cost_usd
    return PhaseResult(
        verdict="passed",
        exit_code=exit_codes.EXIT_OK,
        phase=phase,
        session_id=session_id,
        started_at=started_at,
        ended_at=_now_iso_z(),
        cost_usd=total_cost,
        model_id=result.call2_model_id or result.call1_model_id,
    )


def spawn_retry_loop_phase(
    plan_dir: Path,
    *,
    slug: str,
    chain_id: str,
    phase: int,
    inject_text: str | None = None,
) -> PhaseResult:
    """Dispatch a retry-loop phase (phase=4 /code; phase=5 /test).

    Per implplan §A.impl.9 + verifier_sdk_wiring §T8: this is the
    architectural keystone where the SDK wiring connects to the chain
    driver. The earlier subprocess-based shape (which spawned
    ``bin._retry_loop.main`` as a child process and parsed its exit
    code) is gone — the new shape calls ``run_test_step_loop`` directly
    in-process with the T1-T6 SDK spawners injected as DI'd callables.

    Behaviour
    ---------

    1. **Smoke-check first** — call ``smoke_check_sdk_available`` from
       ``bin._retry_loop.sdk_spawners``. If ``(False, msg)``, short-
       circuit to a ``PhaseResult`` with exit 16
       (``EXIT_VERIFY_PLAN_REJECTED``) and ``cost_usd=0.0`` WITHOUT
       calling ``run_test_step_loop``. The diagnostic msg flows into
       ``halt_reason`` so the operator triage signal lands in the
       morning-review entry.
    2. **Stage hook env vars in driver's ``os.environ``** — NOT a
       subprocess env dict, since there's no subprocess. The SDK
       spawners (T4 / T5) read ``SPLOCK_PLAN_SLUG`` / ``SPLOCK_CHAIN_ID`` /
       ``SPLOCK_PHASE`` from the driver process's environment via
       ``os.environ.get`` and propagate them into the spawned CLI
       subprocess via ``ClaudeAgentOptions.env``. Use try/finally to
       restore the prior state so a test-step retry phase doesn't leak
       env state to subsequent phases.
    3. **Call ``run_test_step_loop`` in-process** with the SDK spawners
       as DI'd kwargs. The spawner functions are passed by IDENTITY (not
       wrapped in lambdas) so test #1 can verify the wire-up via ``is``.
    4. **Map exceptions to exit codes** — ``ReviewerEmissionExhausted``
       → exit 16 (same family as ``PlannerEmissionExhausted`` per
       A.impl.3a). Don't let the exception propagate uncaught; convert
       to a ``PhaseResult`` and let the chain driver's halt-emit machinery
       record it via the normal path.
    5. **Map terminal shapes to exit codes**:
       - ``IterationResult.PASSED`` → exit 0
       - ``IterationResult.HALT_TAMPERING`` → exit 17
         (``EXIT_RETRY_EXCEEDED``-family; tampering is structurally the
         same operator-handoff signal as retry-exceeded)
       - ``IterationResult.HALT_CAP_EXHAUSTED`` → exit 17
         (``EXIT_RETRY_EXCEEDED``)
       - any other → exit 10 (``EXIT_PHASE_BOUNDARY_HALT``)
    6. **Build PhaseResult** with the aggregated ``total_cost_usd`` from
       the 3-tuple return.
    """
    if phase not in (4, 5):
        raise ValueError(f"spawn_retry_loop_phase requires phase in (4, 5); got {phase}")
    session_id = _resolve_session_id(chain_id, phase)
    started_at = _now_iso_z()

    # Lazy-import inside the function so the chain driver imports
    # phase_spawn.py cleanly even when the SDK isn't installed. The
    # smoke check below catches the missing-SDK case BEFORE any of the
    # SDK-dependent surfaces are touched.
    from bin._retry_loop.iteration_loop import (
        IterationResult,
        run_test_step_loop,
    )
    from bin._retry_loop.opus_adapter import (
        build_adapters,
        hook_env_staged,
    )
    from bin._retry_loop.sdk_spawners import (
        ReviewerEmissionExhausted,
        smoke_check_sdk_available,
    )

    # Step 1: SDK smoke check — short-circuit on missing/broken SDK so
    # the chain driver burns one exit-16 instead of a full retry loop.
    sdk_ok, sdk_msg = smoke_check_sdk_available()
    if not sdk_ok:
        logger.warning(
            "smoke_check_sdk_available returned False; "
            "short-circuiting phase %s to exit 16: %s",
            phase, sdk_msg,
        )
        return PhaseResult(
            verdict="verify_plan_rejected",
            exit_code=exit_codes.EXIT_VERIFY_PLAN_REJECTED,
            phase=phase,
            session_id=session_id,
            started_at=started_at,
            ended_at=_now_iso_z(),
            cost_usd=0.0,
            model_id="(sdk-smoke-failed)",
            halt_reason=f"sdk_smoke_failed: {sdk_msg}",
        )

    # Step 2: stage hook env vars on os.environ via the shared context
    # manager. Per Phase 2 post-phase B-1 fix + 2026-05-24 operator-
    # direct wiring fix: §G's runtime hooks (chain-suppression-block.sh
    # + chain-test-file-edit-flag.sh) activate during the test-step
    # retry window when these env vars are visible to the SDK spawners.
    # The context manager restores prior state so a phase 4 → phase 5
    # → phase-6 boundary review chain doesn't leak env values across
    # phases. Centralized in opus_adapter.hook_env_staged so the
    # operator-direct CLI path (bin/_retry_loop/main.py::_run_test_step)
    # shares the same staging logic.
    with hook_env_staged(slug=slug, chain_id=chain_id, phase=phase):
        # Step 3: build the SDK-spawner adapters via the shared factory.
        # The factory owns the impedance match between iteration_loop's
        # call shape (plan_dir / slug / chain_id / iteration_n / ...) and
        # the SDK spawners' call shape (prompt / cwd / hook_env).
        # Centralized in opus_adapter.build_adapters so the operator-
        # direct CLI path shares the same adapter wiring.
        _opus_adapter, _verify_adapter, _reviewer_adapter = build_adapters(
            slug=slug,
            chain_id=chain_id,
            phase=phase,
        )

        try:
            result, records, total_cost_usd = run_test_step_loop(
                plan_dir,
                slug=slug,
                chain_id=chain_id,
                spawn_opus_fn=_opus_adapter,
                run_verify_fn=_verify_adapter,
                spawn_reviewer_fn=_reviewer_adapter,
                # Per R-inject-wiring (CCOR.1): the phase-boundary
                # consume result is threaded into the FIRST iteration's
                # opus spawn. Subsequent iterations within this phase
                # call the per-iteration consume helper inside the
                # iteration loop (which returns None on the
                # second-and-later calls because the file is single-shot).
                initial_inject_text=inject_text,
            )
        except ReviewerEmissionExhausted as exc:
            # Step 4: reviewer SDK retry exhaustion → exit 16. Mirrors
            # the planner's PlannerEmissionExhausted → exit 16 mapping
            # in spawn_planner_phase (per A.impl.3a's shared schema-
            # related halt family).
            logger.warning(
                "ReviewerEmissionExhausted in phase %s: subtype=%s",
                phase, getattr(exc, "subtype", "(no subtype)"),
            )
            return PhaseResult(
                verdict="verify_plan_rejected",
                exit_code=exit_codes.EXIT_VERIFY_PLAN_REJECTED,
                phase=phase,
                session_id=session_id,
                started_at=started_at,
                ended_at=_now_iso_z(),
                cost_usd=0.0,
                model_id="(reviewer-exhausted)",
                halt_reason="reviewer_emission_exhausted",
            )

    # Step 5: map IterationResult → exit code + verdict.
    ended = _now_iso_z()
    if result == IterationResult.PASSED:
        return PhaseResult(
            verdict="passed",
            exit_code=exit_codes.EXIT_OK,
            phase=phase,
            session_id=session_id,
            started_at=started_at,
            ended_at=ended,
            cost_usd=total_cost_usd,
            model_id="(retry-loop)",
        )
    if result == IterationResult.HALT_TAMPERING:
        return PhaseResult(
            verdict="retry_exceeded",
            exit_code=exit_codes.EXIT_RETRY_EXCEEDED,
            phase=phase,
            session_id=session_id,
            started_at=started_at,
            ended_at=ended,
            cost_usd=total_cost_usd,
            model_id="(retry-loop)",
            halt_reason="halt_tampering",
        )
    if result == IterationResult.HALT_CAP_EXHAUSTED:
        return PhaseResult(
            verdict="retry_exceeded",
            exit_code=exit_codes.EXIT_RETRY_EXCEEDED,
            phase=phase,
            session_id=session_id,
            started_at=started_at,
            ended_at=ended,
            cost_usd=total_cost_usd,
            model_id="(retry-loop)",
            halt_reason="halt_cap_exhausted",
        )
    # Unknown terminal shape — generic phase-boundary halt.
    return PhaseResult(
        verdict="phase_boundary_halt",
        exit_code=exit_codes.EXIT_PHASE_BOUNDARY_HALT,
        phase=phase,
        session_id=session_id,
        started_at=started_at,
        ended_at=ended,
        cost_usd=total_cost_usd,
        model_id="(retry-loop)",
        halt_reason=f"unknown_iteration_result: {result!r}",
    )


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------


def _map_planner_exception(
    exc: Exception,
    *,
    chain_id: str,
    phase: int,
    session_id: str,
    started_at: str,
) -> PhaseResult:
    """Map a §D exception to a PhaseResult.

    PlannerEmissionExhausted → exit 16. PlannerBudgetRefused arm
    retired in `delete_usage_caps` (2026-05-23). Any other exception
    bubbles up as exit 2 (driver crash).
    """
    from bin._planner.two_call import PlannerEmissionExhausted

    ended = _now_iso_z()
    if isinstance(exc, PlannerEmissionExhausted):
        return PhaseResult(
            verdict="verify_plan_rejected",
            exit_code=exit_codes.EXIT_VERIFY_PLAN_REJECTED,
            phase=phase,
            session_id=session_id,
            started_at=started_at,
            ended_at=ended,
            cost_usd=0.0,
            model_id="(planner-exhausted)",
            halt_reason="planner_emission_exhausted",
        )
    # Unknown exception — bubble up to main()'s top-level handler.
    raise exc


def _build_planner_inputs(plan_dir: Path, slug: str, step: str):
    """Build PlannerInputs from per-slug MD files (mirrors bin/_planner/main.py)."""
    from bin._planner.two_call import PlannerInputs

    def _read_optional(name: str) -> str:
        p = plan_dir / name
        if not p.exists():
            return ""
        return p.read_text(encoding="utf-8")

    prior_plan_json: str | None = None
    if step == "implplan":
        p = plan_dir / f"{slug}_plan.json"
        if p.exists():
            prior_plan_json = p.read_text(encoding="utf-8")

    return PlannerInputs(
        recon_findings=_read_optional(f"{slug}_recon.md"),
        qa_findings=_read_optional(f"{slug}_qa.md"),
        research_findings=_read_optional(f"{slug}_research.md"),
        lessons_findings=_read_optional("lessons.md"),
        repo_state_summary=os.environ.get(
            "OVERNIGHT_REPO_STATE_SUMMARY",
            "(no repo-state summary provided by chain driver)",
        ),
        prior_plan_json=prior_plan_json,
        tier=os.environ.get("OVERNIGHT_TIER", "Tier 2"),
    )


def _output_target(plan_dir: Path, slug: str, step: str) -> Path:
    """Resolve the planner output JSON path."""
    if step == "plan":
        return plan_dir / f"{slug}_plan.json"
    return plan_dir / f"{slug}_orchestrator.json"


def _default_verify_plan(slug: str, *, kind: str) -> int:
    """Invoke `bin/verify_plan --strict <slug>` as a subprocess.

    Returns the exit code. Caller maps non-zero to a PhaseResult halt.
    """
    repo_root = _repo_root()
    cmd = [
        sys.executable,
        "-m",
        "bin._render_plan.verify",
        slug,
        "--kind",
        "plan" if kind == "plan" else "orchestrator",
        "--strict",
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("bin/verify_plan invocation failed: %s", exc)
        return 1
    return result.returncode


def _repo_root() -> Path:
    """Walk up from this file to the repo root (parent of bin/)."""
    return Path(__file__).resolve().parents[2]


__all__ = [
    "PHASE_COMMAND_MAP",
    "PhaseCommand",
    "PhaseResult",
    "precheck_caps",
    "recheck_foreign_sentinel",
    "spawn_planner_phase",
    "spawn_retry_loop_phase",
]
