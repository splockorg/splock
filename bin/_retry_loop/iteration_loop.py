"""Test-step iteration orchestrator — Opus → bin/verify → reviewer.

Per splock plan §F.3 + implplan §F.impl.3. The chain driver
(§A.impl.9) invokes `bin/_retry_loop.main` once per test-step entry;
`run_iteration` is the inner per-iteration orchestrator that
`main.run_test_step_loop` calls in a bounded loop.

Anchor §4a.3 elements 1 + 2 ship here
--------------------------------------

Element 1 (unified retry counter): every iteration spawned in this
module counts against the per-task ``retry_count`` counter shared with
Ralph (NO/READY) and the phase-boundary reviewer (NEEDS_REVISION). The
counter is read + incremented via the helpers `unified_counter_*`
(implemented inline in this module — there is intentionally no
separate `unified_counter.py` per spec §F.impl.9; the helpers are
co-located with the iteration loop that mutates them).

Element 2 (structured-rubric verdict shape): the reviewer's response
arrives as a structured dict with R1-R5 keys per `rubric.py`
TEST_STEP_RUBRIC_SCHEMA_V1. The iteration loop's dispatch is purely a
function of R4 (load-bearing tampering check) and counter state — no
free-form Sonnet prose drives control flow.

RUNTIME ≠ build-time
--------------------

This module is the RUNTIME §F.3 test-step retry loop inside
`bin/chain-overnight`. It is NOT the build-time orchestrator §5
mid-section review junction that ran when this very module was being
constructed. The build-time junction emitted BLOCKER / MAJOR / MINOR
findings against this code; the runtime loop emits READY-equivalent
``IterationResult.PASSED`` / NEEDS_REVISION-equivalent
``IterationResult.FAILED_RETRY`` / HALT-equivalent
``IterationResult.HALT_*`` against the agent-emitted code under test.
"""

from __future__ import annotations

import enum
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

from . import briefing as briefing_mod, exit_codes, halt_handoff, rubric as rubric_mod

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Closed-enum result + retry-counter source classifiers
# ----------------------------------------------------------------------

class IterationResult(enum.Enum):
    """Closed enum returned by `run_iteration`.

    Mirrors implplan §F.impl.3 entry signature: PASSED / FAILED_RETRY /
    HALT_TAMPERING / HALT_CAP_EXHAUSTED.
    """

    PASSED = "passed"
    FAILED_RETRY = "failed_retry"
    HALT_TAMPERING = "halt_tampering"
    HALT_CAP_EXHAUSTED = "halt_cap_exhausted"


CounterSource = Literal["ralph_no", "reviewer_needs_revision", "test_step_retry"]
"""Closed enum for the unified counter's source-field discipline per
§F.impl.9. Values are forensic — they live in `_orchestrator_log.jsonl`
rows, NOT in `_state.json` (which carries a single integer per task).
"""


# ----------------------------------------------------------------------
# Iteration recording — per-iteration audit row for morning-review entry
# ----------------------------------------------------------------------

@dataclass
class IterationRecord:
    """One iteration of forensic context for the halt-handoff entry.

    Per §F.6: morning-review entries enumerate iterations 1..N with
    each iteration's failing tests / Opus diff / Sonnet rubric. This
    dataclass is the in-memory accumulator.

    `cost_usd` is the per-iteration USD spend (coder + reviewer SDK
    sessions summed). Added by T7 of verifier_sdk_wiring so the
    aggregator in `run_test_step_loop` can produce a phase-total cost
    that the chain driver surfaces to morning-review. Optional with
    default `None` so existing callers that don't supply cost (legacy
    tests, build-time fixtures) keep constructing IterationRecord
    cleanly.

    `repair_scope_violations` mirrors the `cost_usd` pattern: the T8
    repair write-scope guard (real_tests_at_junctions SC8,
    `sdk_spawners.enforce_repair_write_scope`) returns its
    reverted/flagged verdicts on the coder spawner result dict under
    `'repair_scope_violations'`; the iteration loop threads them onto
    the record so halt forensics carry the guard's evidence. Optional
    with default `None` (legacy fixtures, verify-only entry records).
    """

    iteration_n: int
    started_at: str
    ended_at: str
    test_runner_exit_code: int
    failing_tests: list[str]
    diff_excerpt: str
    rubric: dict[str, Any] | None = None
    reviewer_model: str | None = None
    cost_usd: float | None = None
    repair_scope_violations: list[dict[str, str]] | None = None


# ----------------------------------------------------------------------
# Public entry: run_iteration (one iteration; called in a loop by main)
# ----------------------------------------------------------------------

@dataclass
class IterationContext:
    """Bundle of context passed through per-iteration calls.

    Created by `main` once per test-step entry; mutated as iterations
    progress (records appended, retry_count incremented).

    `pending_inject_text` carries the operator's framed inject body
    when an `_operator_inject.md` consume happened at the phase-boundary
    OR at the previous iteration. The next opus spawn picks it up;
    after that spawn, the field is cleared (single-shot per
    R-inject-single-shot).
    """

    plan_dir: Path
    slug: str
    chain_id: str
    iteration_n: int
    max_retries: int
    records: list[IterationRecord] = field(default_factory=list)
    prior_diagnosis: dict[str, Any] | None = None
    pending_inject_text: str | None = None


def run_iteration(
    ctx: IterationContext,
    *,
    spawn_opus_fn: Callable[..., dict] | None = None,
    run_verify_fn: Callable[..., subprocess.CompletedProcess] | None = None,
    spawn_reviewer_fn: Callable[..., dict] | None = None,
) -> IterationResult:
    """Run a single test-step iteration.

    Pipeline per §F.impl.3:
    1. Spawn Opus step agent (hooks pre-configured).
    2. Run `bin/verify` POSIX wrapper around pytest.
    3. If exit 0: emit transition row, return PASSED.
    4. If exit != 0:
       a. Construct deterministic briefing via `briefing.build_briefing`.
       b. Spawn reviewer subagent with structured-output schema.
       c. If R4 == "yes-flagged" → HALT_TAMPERING.
       d. Otherwise store prior_diagnosis, return FAILED_RETRY.

    Dependency-injected helpers default to module-level real surfaces
    when None; tests pass mocks to avoid live SDK calls / subprocess
    spawns.
    """
    if spawn_opus_fn is None:
        spawn_opus_fn = _default_spawn_opus
    if run_verify_fn is None:
        run_verify_fn = _default_run_verify
    if spawn_reviewer_fn is None:
        spawn_reviewer_fn = _default_spawn_reviewer

    started_at = _now_iso_z()

    # Per R-inject-wiring + R-inject-single-shot (CCOR.1
    # design_resolutions) and userguide §3.6: at the opus iter spawn
    # site (THIS spawn — not the reviewer spawn below), consume any
    # pending `_operator_inject.md` so the operator's framed
    # correction text lands in the next coder prompt. The
    # `pending_inject_text` carried by IterationContext wins if it's
    # already set (phase-boundary consume); otherwise we attempt a
    # per-iteration consume here. Single-shot — after the spawn we
    # clear the field.
    #
    # The reviewer spawn (Step 4b below) intentionally does NOT
    # consume / receive the inject: reviewer is structured-output-only
    # and the inject contract is not defined for verdict-only prompts.
    # Shipping reviewer-inject would invalidate the determinism
    # property declared in userguide §3.6.
    iter_inject_text = ctx.pending_inject_text
    if iter_inject_text is None:
        iter_inject_text = _consume_operator_inject_iter(
            ctx.plan_dir, ctx.chain_id, slug=ctx.slug,
        )
    ctx.pending_inject_text = None

    # Step 1: spawn Opus step agent. Tests pass a mock; the real call
    # is a subprocess.Popen with env-staged SPLOCK_PLAN_SLUG / SPLOCK_CHAIN_ID
    # / SPLOCK_PHASE=5 so the runtime hooks activate.
    opus_result = _call_opus_spawner(
        spawn_opus_fn,
        plan_dir=ctx.plan_dir,
        slug=ctx.slug,
        chain_id=ctx.chain_id,
        iteration_n=ctx.iteration_n,
        prior_diagnosis=ctx.prior_diagnosis,
        inject_text=iter_inject_text,
    )

    # Step 2: run bin/verify.
    verify_result = run_verify_fn(
        plan_dir=ctx.plan_dir,
        iteration_n=ctx.iteration_n,
    )
    exit_code = verify_result.returncode
    diff_excerpt = opus_result.get("diff_excerpt", "")
    failing_tests = _extract_failing_tests(verify_result.stdout or "")

    if exit_code == 0:
        # Step 3: PASSED — emit transition, return.
        # No reviewer spawn on PASSED path → iteration cost is coder-only.
        # Coder spawner returns 'cost_usd' per T5; treat missing/None as 0.0
        # so the legacy stub fixtures (which don't bother with cost) work.
        iteration_cost = _coerce_cost(opus_result.get("cost_usd"))
        _emit_iteration_transition(
            ctx,
            transition_to="done",
            reason=f"test_step iteration {ctx.iteration_n} PASSED",
        )
        record = IterationRecord(
            iteration_n=ctx.iteration_n,
            started_at=started_at,
            ended_at=_now_iso_z(),
            test_runner_exit_code=0,
            failing_tests=[],
            diff_excerpt=diff_excerpt,
            rubric=None,
            cost_usd=iteration_cost,
            repair_scope_violations=opus_result.get("repair_scope_violations"),
        )
        ctx.records.append(record)
        return IterationResult.PASSED

    # Step 4a: deterministic briefing — NEVER agent-authored narrative.
    prompt = briefing_mod.build_briefing(
        slug=ctx.slug,
        iteration_n=ctx.iteration_n,
        rubric_kind="test_step",
        plan_dir=ctx.plan_dir,
        prior_diagnosis=ctx.prior_diagnosis,
        iteration_metadata={
            "test_files_edited_this_iteration": opus_result.get(
                "test_files_edited", []
            ),
            "test_runner_exit_code": exit_code,
            "iteration_diff_lines_added": opus_result.get("diff_lines_added", 0),
            "iteration_diff_lines_removed": opus_result.get(
                "diff_lines_removed", 0
            ),
        },
        debug_echo=os.environ.get("OVERNIGHT_DEBUG_RETRY_PROMPT") == "1",
    )

    # Step 4b: spawn reviewer subagent.
    rubric_payload = spawn_reviewer_fn(
        plan_dir=ctx.plan_dir,
        prompt=prompt,
        rubric_kind="test_step",
    )

    # Schema validation — refuse unknown rubric versions per §F.impl.5.
    version = rubric_payload.get("rubric_version", 1)
    if not rubric_mod.is_supported_version("test_step", version):
        raise rubric_mod.UnsupportedRubricVersionError(
            kind="test_step",
            version=version,
            supported=[1],
        )

    # FAILED path: cost is coder + reviewer summed. The coder spawner
    # emits 'cost_usd' (T5); the reviewer spawner emits '_sdk_cost_usd'
    # under the underscore prefix so the rubric schema's
    # additionalProperties=false stays valid (T4). Treat missing/None
    # as 0.0 so stub-fixtures without cost keys keep working.
    iteration_cost = _coerce_cost(opus_result.get("cost_usd")) + _coerce_cost(
        rubric_payload.get("_sdk_cost_usd")
    )

    record = IterationRecord(
        iteration_n=ctx.iteration_n,
        started_at=started_at,
        ended_at=_now_iso_z(),
        test_runner_exit_code=exit_code,
        failing_tests=failing_tests,
        diff_excerpt=diff_excerpt,
        rubric=rubric_payload,
        reviewer_model=rubric_payload.get("_reviewer_model"),
        cost_usd=iteration_cost,
        repair_scope_violations=opus_result.get("repair_scope_violations"),
    )
    ctx.records.append(record)

    # Step 4c: R4 load-bearing tampering check.
    if rubric_mod.is_tampering_flagged(rubric_payload):
        return IterationResult.HALT_TAMPERING

    # Step 4d: store prior_diagnosis and signal "loop continues".
    ctx.prior_diagnosis = rubric_payload
    return IterationResult.FAILED_RETRY


# ----------------------------------------------------------------------
# Public entry: run_test_step_loop (the bounded iteration driver)
# ----------------------------------------------------------------------

def run_test_step_loop(
    plan_dir: Path,
    *,
    slug: str,
    chain_id: str,
    max_retries: int | None = None,
    spawn_opus_fn: Callable[..., dict] | None = None,
    run_verify_fn: Callable[..., subprocess.CompletedProcess] | None = None,
    spawn_reviewer_fn: Callable[..., dict] | None = None,
    initial_inject_text: str | None = None,
    verify_first: bool = False,
) -> tuple[IterationResult, list[IterationRecord], float]:
    """Bounded iteration loop.

    Returns ``(final_result, iteration_records, total_cost_usd)``. The
    caller (`main` or `phase_spawn.spawn_retry_loop_phase`) maps the
    result to an exit code, surfaces the cost to morning-review, and
    writes the halt entry if non-PASSED.

    The third tuple element was added by T7 of verifier_sdk_wiring —
    it sums ``IterationRecord.cost_usd`` across every appended record
    (coder + reviewer SDK spend per iteration). Records that carry
    ``cost_usd=None`` contribute 0.0 so legacy fixtures still aggregate
    cleanly.

    Per §F.impl.3:
    - Default ``max_retries`` from ``OVERNIGHT_TEST_MAX_RETRIES`` (default 3).
    - Warning on > 3 per Finding 23 (logged but not enforced).
    - **Unified counter**: every iteration increments per-task
      ``retry_count`` via `unified_counter_increment`. Cap selection per
      `unified_counter_active_cap` (default=3; OVERNIGHT_MODE=1 → 6).

    Anchor §4a.3 element 1 sanity check — the unified counter is the
    single budget per task. Ralph (NO) + reviewer (NEEDS_REVISION) +
    test-step retries all consume from the same number. The cap clamp
    composition: ``min(test_max_retries, remaining_unified_budget)``.

    ``verify_first`` (verify-first reorder, real_tests_at_junctions
    follow-up 2026-06-10): when True, run the verify subprocess ONCE
    before the first repair spawn. A GREEN entry verify (returncode 0)
    short-circuits to PASSED with zero opus / reviewer spawns — the two
    live fabrication incidents (2026-06-01, 2026-06-10) both exploited
    the idle iteration-1 repair window that exists when the suite is
    already green; T8 made that window harmless (write-scope revert),
    this makes it absent. A RED entry verify (any non-zero returncode,
    including the T8 UNTRUSTED-GREEN coercion to 13) falls through to
    the legacy loop unchanged: no IterationRecord is emitted for the
    entry verify and the unified counter is NOT incremented (the entry
    verify is not a repair attempt). The short-circuit is BYPASSED
    entirely when an operator inject is pending (``initial_inject_text``
    set, or ``_operator_inject.md`` on disk) — otherwise a green entry
    would silently swallow the single-shot inject that iteration 1's
    repair spawn is contracted to deliver. Default False preserves
    legacy behavior byte-for-byte; the phase-4 ``/code`` dispatch MUST
    NOT opt in (a doc-only task with trivially-green tests would
    "pass" without ever spawning its task coder).
    """
    if max_retries is None:
        max_retries = _resolve_max_retries()
    if max_retries > 3:
        logger.warning(
            "OVERNIGHT_TEST_MAX_RETRIES=%d > 3; accuracy may drop sharply "
            "per arXiv:2603.08877 (industry default is 3)",
            max_retries,
        )

    # Determine the unified-counter cap; subset-clamp to max_retries.
    unified_cap = unified_counter_active_cap()
    unified_remaining = unified_counter_get_remaining(
        plan_dir, task_id="test_step", cap=unified_cap,
    )
    effective_max = min(max_retries, unified_remaining)
    if effective_max < 1:
        # Already exhausted by Ralph or reviewer counter.
        return IterationResult.HALT_CAP_EXHAUSTED, [], 0.0

    ctx = IterationContext(
        plan_dir=plan_dir,
        slug=slug,
        chain_id=chain_id,
        iteration_n=1,
        max_retries=effective_max,
        pending_inject_text=initial_inject_text,
    )

    # Verify-first reorder — see the docstring paragraph above. The
    # inject-pending bypass checks BOTH delivery channels: the
    # phase-boundary consume result (`initial_inject_text`) and the
    # on-disk `_operator_inject.md` that iteration 1's per-iteration
    # consume would otherwise pick up.
    if verify_first:
        inject_pending = initial_inject_text is not None or (
            plan_dir / _OPERATOR_INJECT_FILENAME
        ).exists()
        if not inject_pending:
            entry_verify_fn = (
                run_verify_fn if run_verify_fn is not None else _default_run_verify
            )
            entry_started_at = _now_iso_z()
            # Same call seam the loop uses (run_verify_fn(plan_dir=,
            # iteration_n=)); iteration_n=0 keeps the entry verify's
            # `_test_output_iter0.txt` artifact distinct from the loop's
            # iteration 1 output.
            entry_result = entry_verify_fn(plan_dir=plan_dir, iteration_n=0)
            if entry_result.returncode == 0:
                ctx.iteration_n = 0
                _emit_iteration_transition(
                    ctx,
                    transition_to="done",
                    reason="entry verify PASSED (verify_first short-circuit)",
                )
                ctx.records.append(
                    IterationRecord(
                        iteration_n=0,
                        started_at=entry_started_at,
                        ended_at=_now_iso_z(),
                        test_runner_exit_code=0,
                        failing_tests=[],
                        diff_excerpt="",
                        rubric=None,
                        cost_usd=0.0,
                    )
                )
                return (
                    IterationResult.PASSED,
                    ctx.records,
                    _sum_iteration_costs(ctx.records),
                )
            # RED entry (incl. UNTRUSTED-GREEN rc=13): discard the entry
            # result and fall through to the legacy loop unchanged. The
            # repair briefing's failure context is rubric-shaped
            # (prior_diagnosis) — there is no clean seam to hand raw
            # verify output to iteration 1, so iteration 1 re-runs the
            # suite itself exactly as today.
            ctx.iteration_n = 1

    last_result: IterationResult = IterationResult.HALT_CAP_EXHAUSTED
    for iter_n in range(1, effective_max + 1):
        ctx.iteration_n = iter_n
        # Bump unified counter BEFORE the iteration runs so a crash
        # doesn't desync the per-task budget.
        unified_counter_increment(
            plan_dir,
            task_id="test_step",
            source="test_step_retry",
        )
        last_result = run_iteration(
            ctx,
            spawn_opus_fn=spawn_opus_fn,
            run_verify_fn=run_verify_fn,
            spawn_reviewer_fn=spawn_reviewer_fn,
        )
        if last_result == IterationResult.PASSED:
            return last_result, ctx.records, _sum_iteration_costs(ctx.records)
        if last_result == IterationResult.HALT_TAMPERING:
            return last_result, ctx.records, _sum_iteration_costs(ctx.records)
        # FAILED_RETRY → loop continues to next iter.
    # Loop fell through without PASSED → cap exhausted.
    return (
        IterationResult.HALT_CAP_EXHAUSTED,
        ctx.records,
        _sum_iteration_costs(ctx.records),
    )


# ----------------------------------------------------------------------
# Unified retry counter — anchor §4a.3 element 1 enforcement
# ----------------------------------------------------------------------

def unified_counter_active_cap() -> int:
    """Return the unified retry cap based on ``OVERNIGHT_MODE``.

    Per §F.impl.9 / plan §F.9.4 / §1.I unified counter:
    - default (unset / ``OVERNIGHT_MODE=0``) → 3
    - overnight (``OVERNIGHT_MODE=1``) → 6
    """
    raw = os.environ.get("OVERNIGHT_MODE", "")
    if raw == "1":
        return 6
    return 3


def unified_counter_increment(
    plan_dir: Path,
    *,
    task_id: str,
    source: CounterSource,
) -> int:
    """Atomically increment per-task ``retry_count`` in ``_state.json``.

    Per §F.impl.9 source-field discipline: ``source`` is NOT stored on
    ``_state.json`` — it goes into a `_orchestrator_log.jsonl` row via
    §C's append_row (the iteration loop emits this from
    `_emit_iteration_transition`). This function ONLY mutates the
    single integer counter and returns the post-increment value.

    Per cross-cutting `flock` discipline: read-modify-write on
    `_state.json` wraps in flock on `<plan_dir>/_state.json.lock`.
    """
    state_path = plan_dir / "_state.json"
    lock_path = plan_dir / "_state.json.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Acquire lock for the full RMW cycle.
    import fcntl  # POSIX-only — WSL2/Ubuntu per CLAUDE.md execution-env rule

    with open(lock_path, "a") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            state: dict[str, Any]
            if state_path.exists():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    state = {}
            else:
                state = {}
            tasks = state.setdefault("tasks", {})
            if not isinstance(tasks, dict):
                tasks = {}
                state["tasks"] = tasks
            entry = tasks.setdefault(task_id, {})
            if not isinstance(entry, dict):
                entry = {}
                tasks[task_id] = entry
            new_count = int(entry.get("retry_count", 0)) + 1
            entry["retry_count"] = new_count

            # Atomic write — temp + rename.
            from bin._render_plan.atomic_write import write_atomic

            body = json.dumps(state, indent=2, sort_keys=True) + "\n"
            write_atomic(state_path, body)
            return new_count
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def unified_counter_get_remaining(
    plan_dir: Path,
    *,
    task_id: str,
    cap: int | None = None,
) -> int:
    """Return remaining unified budget for `task_id` against `cap`.

    Per §F.impl.9: returns `cap - retry_count` (clamped to >= 0).
    """
    if cap is None:
        cap = unified_counter_active_cap()
    state_path = plan_dir / "_state.json"
    if not state_path.exists():
        return cap
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return cap
    tasks = state.get("tasks", {})
    if not isinstance(tasks, dict):
        return cap
    entry = tasks.get(task_id, {})
    if not isinstance(entry, dict):
        return cap
    used = int(entry.get("retry_count", 0))
    return max(0, cap - used)


def unified_counter_reset(plan_dir: Path, *, task_id: str) -> int:
    """Reset per-task ``retry_count`` to 0 in ``_state.json``; return the
    prior value.

    The SANCTIONED operator reset behind `bin/verify boundary --fresh`
    (first field deployment, 2026-07-19): the counter survives a
    cap-exhaustion halt by design, but the only reset path used to be
    the full ``bin/chain-overnight --from-resume`` machinery — a
    heavyweight vehicle for "re-run one review" — while a hand edit of
    the sealed `_state.json` is (correctly) hook-blocked. This helper
    keeps the seal intact: same module, same flock RMW discipline as
    :func:`unified_counter_increment`; the CLI layer logs the reset to
    `_orchestrator_log.jsonl` so the audit trail records the intent.
    """
    state_path = plan_dir / "_state.json"
    lock_path = plan_dir / "_state.json.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    import fcntl  # POSIX-only — WSL2/Ubuntu per CLAUDE.md execution-env rule

    with open(lock_path, "a") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            state: dict[str, Any]
            if state_path.exists():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    state = {}
            else:
                state = {}
            tasks = state.setdefault("tasks", {})
            if not isinstance(tasks, dict):
                tasks = {}
                state["tasks"] = tasks
            entry = tasks.setdefault(task_id, {})
            if not isinstance(entry, dict):
                entry = {}
                tasks[task_id] = entry
            prior = int(entry.get("retry_count", 0))
            entry["retry_count"] = 0

            # Atomic write — temp + rename (same seam as increment).
            from bin._render_plan.atomic_write import write_atomic

            body = json.dumps(state, indent=2, sort_keys=True) + "\n"
            write_atomic(state_path, body)
            return prior
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


# ----------------------------------------------------------------------
# Operator-inject consumption (per-iteration single-shot)
# ----------------------------------------------------------------------


# Operator-inject filename. Must match
# `bin/_chain_overnight/main.py::OPERATOR_INJECT_FILENAME` and
# `bin/_chain_resume/main.py::OPERATOR_INJECT_FILENAME` — the three
# constants travel together.
_OPERATOR_INJECT_FILENAME = "_operator_inject.md"


def _consume_operator_inject_iter(
    plan_dir: Path,
    chain_id: str,
    *,
    slug: str | None = None,
) -> str | None:
    """Per-iteration operator-inject consume (single-shot).

    Per R-inject-wiring + R-inject-single-shot (CCOR.1
    design_resolutions) and userguide §3.6: the OPUS-ITER spawn site
    consumes `_operator_inject.md` (if present). The reviewer spawn
    site (also in this module) does NOT — keeping that for forensic
    reasons + because reviewer is structured-output-only.

    Emits a `pause_inject_consumed` log row when an actual consume
    happened. Best-effort; an emit failure is forensic-only.

    Args:
        plan_dir: Plan directory.
        chain_id: Chain id for the log row.
        slug: Plan slug for the log row's `plan_slug` field.

    Returns:
        The framed inject body if consumed; None if the file was absent.
    """
    target = plan_dir / _OPERATOR_INJECT_FILENAME
    if not target.exists():
        return None
    try:
        body = target.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "operator-inject read failed (opus-iter) plan_dir=%s: %s",
            plan_dir, exc,
        )
        return None
    try:
        target.unlink()
    except FileNotFoundError:
        return body
    except OSError as exc:
        logger.warning(
            "operator-inject delete failed (opus-iter) plan_dir=%s: %s",
            plan_dir, exc,
        )
    # Emit `pause_inject_consumed` log row (best-effort).
    try:
        from bin._jsonl_log import append_row
        import hashlib

        try:
            inject_size_bytes = len(body.encode("utf-8"))
        except Exception:  # noqa: BLE001 — defensive
            inject_size_bytes = len(body)
        digest = hashlib.sha1(f"driver|{chain_id}".encode("utf-8")).hexdigest()
        session_id = f"sess_{digest[:8]}"
        row = {
            "session_id": session_id,
            "plan_slug": slug,
            "chain_id": chain_id,
            "task_id": None,
            "transition": {"from": "wip", "to": "wip"},
            "mode_at_transition": {"overnight": True, "guardrail": False},
            "reason": (
                f"pause_inject_consumed inject_size_bytes={inject_size_bytes}"
            ),
            "event_type": "pause_inject_consumed",
            "inject_size_bytes": inject_size_bytes,
        }
        append_row(plan_dir, row, emitted_by="bin/chain-overnight")
    except Exception:  # noqa: BLE001 — best-effort log emit
        logger.warning(
            "pause_inject_consumed log row emit failed (opus-iter)",
            exc_info=True,
        )
    return body


def _call_opus_spawner(
    spawn_opus_fn: Callable[..., dict],
    *,
    plan_dir: Path,
    slug: str,
    chain_id: str,
    iteration_n: int,
    prior_diagnosis: dict[str, Any] | None,
    inject_text: str | None,
) -> dict:
    """Call the injected opus spawner with `inject_text` if it accepts it.

    Some opus spawner implementations (legacy stubs, tests) do not
    accept `inject_text`. Use TypeError fallback to preserve backward
    compatibility with stubs that don't carry the new kwarg.
    """
    try:
        return spawn_opus_fn(
            plan_dir=plan_dir,
            slug=slug,
            chain_id=chain_id,
            iteration_n=iteration_n,
            prior_diagnosis=prior_diagnosis,
            inject_text=inject_text,
        )
    except TypeError:
        # Legacy spawner without inject_text kwarg — call without it.
        # In production, the chain driver's `_opus_adapter` always
        # accepts the kwarg; this fallback only fires in fixture-only
        # test environments.
        return spawn_opus_fn(
            plan_dir=plan_dir,
            slug=slug,
            chain_id=chain_id,
            iteration_n=iteration_n,
            prior_diagnosis=prior_diagnosis,
        )


# ----------------------------------------------------------------------
# Default impls — real surfaces (overridden by tests via DI)
# ----------------------------------------------------------------------

def _default_spawn_opus(
    *,
    plan_dir: Path,
    slug: str,
    chain_id: str,
    iteration_n: int,
    prior_diagnosis: dict[str, Any] | None,
) -> dict[str, Any]:
    """Default coder spawner — refuses to run.

    The retry loop's coder spawner must be injected via DI by the chain
    driver (`bin/_chain_overnight/phase_spawn.py:spawn_retry_loop_phase`
    after T8), which wires `bin._retry_loop.sdk_spawners.spawn_opus_via_sdk`
    as the production implementation. If this default fires, the caller
    bypassed the chain driver — there is no useful behavior to fall back
    to, so we fail fast with a clear message instead of silently producing
    wrong data.
    """
    raise NotImplementedError(
        "_default_spawn_opus is a placeholder; production callers must "
        "inject spawn_opus_fn via the chain-overnight driver "
        "(bin/_chain_overnight/phase_spawn.py::spawn_retry_loop_phase). "
        "If you are testing run_iteration directly, pass a mock "
        "spawn_opus_fn kwarg."
    )


def _default_run_verify(
    *,
    plan_dir: Path,
    iteration_n: int,
) -> subprocess.CompletedProcess:
    """Default test-runner — refuses to run.

    The retry loop's verifier must be injected via DI by the chain
    driver (`bin/_chain_overnight/phase_spawn.py:spawn_retry_loop_phase`
    after T8), which wires `bin._retry_loop.sdk_spawners.run_verify_subprocess`
    as the production implementation. If this default fires, the caller
    bypassed the chain driver — there is no useful behavior to fall back
    to, so we fail fast with a clear message instead of silently producing
    wrong data.
    """
    raise NotImplementedError(
        "_default_run_verify is a placeholder; production callers must "
        "inject run_verify_fn via the chain-overnight driver "
        "(bin/_chain_overnight/phase_spawn.py::spawn_retry_loop_phase). "
        "If you are testing run_iteration directly, pass a mock "
        "run_verify_fn kwarg."
    )


def _default_spawn_reviewer(
    *,
    plan_dir: Path,
    prompt: str,
    rubric_kind: rubric_mod.RubricKind,
) -> dict[str, Any]:
    """Default reviewer spawner — returns a benign sentinel rubric.

    Unlike `_default_spawn_opus` and `_default_run_verify` (which raise
    `NotImplementedError` after T9 of verifier_sdk_wiring), this stub
    intentionally produces a synthetic R1-R5 rubric with R4='unclear'.
    The chain-overnight phase-boundary review path uses this stub
    directly (it does not require a real SDK call), so removing it
    would break the dry-run code path. The SDK-backed production
    reviewer lives at `bin._retry_loop.sdk_spawners.spawn_reviewer_via_sdk`
    and is injected by the chain driver for the test-step retry loop;
    this default stays for the phase-boundary path.

    **§D integration contract.** The reviewer subagent definition
    `.claude/agents/reviewer.md` (shipped by §D.impl.4) describes the
    Sonnet reviewer role. The runtime dispatch path here uses the
    canonical Claude Code Agent tool path — NOT
    `bin._planner.invoke_planner` (that's the planner workflow, not
    the runtime reviewer).

    For tests, dependency injection substitutes a recorded-response
    fixture; the stub here surfaces a clear "real dispatch not wired"
    sentinel so test environments fail loudly rather than silently
    returning a fake READY verdict.

    NOTE: real wiring to the Claude Code Agent tool lives in the chain
    driver layer (it constructs the SDK client; this function gets
    handed the resulting dispatch). The stub returns an "unable to
    spawn" sentinel rubric that the iteration loop treats as a HALT.
    """
    logger.warning(
        "default_spawn_reviewer is a placeholder — chain driver should DI "
        "a real subagent dispatcher (rubric_kind=%s)", rubric_kind,
    )
    # Stub rubric: surfaces a "halt for operator review" sentinel that
    # the loop treats as HALT_TAMPERING-equivalent. In production the
    # chain driver always injects a real dispatcher.
    if rubric_kind == "test_step":
        return {
            "rubric_version": 1,
            "iteration": 1,
            "R1_root_cause": "(reviewer dispatch not wired)",
            "R2_what_missed": "(reviewer dispatch not wired)",
            "R3_next_action": "(reviewer dispatch not wired)",
            "R4_tampering": "unclear",
            "R5_confidence": "low",
            "_metadata": {
                "test_files_edited_this_iteration": [],
                "test_runner_exit_code": -1,
                "iteration_diff_lines_added": 0,
                "iteration_diff_lines_removed": 0,
            },
        }
    return {
        "rubric_version": 1,
        "boundary": "plan_to_implplan",
        "R1_recon_coverage": "gaps_identified",
        "R2_deferred_now_defensibility": "flag",
        "R3_structural_ambiguities": "flag",
        "terminal_shape": "HALT",
    }


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------

def _resolve_max_retries() -> int:
    """Read ``OVERNIGHT_TEST_MAX_RETRIES`` env var with bounds check.

    Default 3 per arXiv:2603.08877 + Claude Code internal
    ``MAX_OUTPUT_TOKENS_RECOVERY_LIMIT``. Range 1-5.
    """
    raw = os.environ.get("OVERNIGHT_TEST_MAX_RETRIES", "")
    if not raw:
        return 3
    try:
        val = int(raw)
    except ValueError:
        return 3
    return max(1, min(5, val))


def _extract_failing_tests(stdout: str) -> list[str]:
    """Heuristically extract failing-test names from pytest stdout."""
    failing: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        # pytest -v line: "tests/path/to::test_name FAILED"
        if "FAILED" in line and "::" in line:
            test_id = line.split(" ")[0]
            if "::" in test_id:
                failing.append(test_id)
        # pytest short summary: "FAILED tests/path/to::test_name"
        elif line.startswith("FAILED "):
            test_id = line.split(" ", 1)[1].split(" ")[0]
            failing.append(test_id)
    return failing


def _now_iso_z() -> str:
    import datetime

    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _coerce_cost(value: Any) -> float:
    """Coerce a per-spawner cost value to a non-negative float.

    Per T7 of verifier_sdk_wiring. The coder spawner returns
    ``cost_usd`` (T5) and the reviewer spawner returns
    ``_sdk_cost_usd`` (T4); either may be absent (legacy stubs) or
    explicitly ``None`` (SDK didn't populate the field). Coerce
    missing / None / non-numeric values to 0.0 so the aggregation
    never crashes on a partial fixture set.
    """
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _sum_iteration_costs(records: Iterable[IterationRecord]) -> float:
    """Sum ``cost_usd`` across an iteration-record list.

    Records with ``cost_usd=None`` contribute 0.0. Added by T7 of
    verifier_sdk_wiring as the aggregator backing the third element
    of `run_test_step_loop`'s return tuple.
    """
    return sum(_coerce_cost(rec.cost_usd) for rec in records)


def _emit_iteration_transition(
    ctx: IterationContext,
    *,
    transition_to: str,
    reason: str,
) -> None:
    """Append a §C transition row for the iteration outcome.

    Uses `bin/verify` as `emitted_by` per §F-owned sub-emitter
    discipline (anchor §4a.3 element 4 wiring).
    """
    try:
        from bin._jsonl_log import append_row

        # _orchestrator_log.jsonl reader expects session_id of canonical
        # shape; synthesize from chain_id + iteration.
        import hashlib

        digest = hashlib.sha1(
            f"{ctx.chain_id}|test_step|{ctx.iteration_n}".encode("utf-8")
        ).hexdigest()
        session_id = f"sess_{digest[:8]}"
        # task_id per orchestrator_log_v1.schema.json must match ^T[0-9]+$
        # or be null; the retry-loop emits null since "test_step" is the
        # phase-level concept (not a per-task id).
        row = {
            "session_id": session_id,
            "plan_slug": ctx.slug,
            "chain_id": ctx.chain_id,
            "task_id": None,
            "transition": {"from": "wip", "to": transition_to},
            "pointer": None,
            "retry_count": ctx.iteration_n,
            "mode_at_transition": {"overnight": True, "guardrail": False},
            "override_in_effect": None,
            "reason": f"test_step iteration {ctx.iteration_n}: {reason}",
            "verifier_verdict_ref": None,
        }
        append_row(ctx.plan_dir, row, emitted_by="bin/verify")
    except Exception as exc:  # noqa: BLE001 — best-effort; halt entry covers forensics
        logger.warning("transition emit failed (best-effort): %s", exc)


__all__ = [
    "CounterSource",
    "IterationContext",
    "IterationRecord",
    "IterationResult",
    "run_iteration",
    "run_test_step_loop",
    "unified_counter_active_cap",
    "unified_counter_get_remaining",
    "unified_counter_increment",
]
