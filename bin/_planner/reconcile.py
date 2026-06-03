"""Per-op-kind reconciliation policy for amending an ACTIVE plan (T5a / §SC5).

Plan `plan_surgical_amend` Phase 2, task **T5a**. This module is the
**decision layer** that sits between the Phase-1 surgical-amend apply engine
(`patch_apply.py`) and a *live* orchestrator/state. In Phase 1, `--amend`
deliberately BYPASSES the cascade refusal so an operator can amend a
`<slug>_plan.json` that has already been promoted to an active
`<slug>_orchestrator.json`. T5a makes that bypass SAFE by deciding, **per patch
op**, how it reconciles against the in-flight task graph:

  * **task mutation (replace / remove) of an in-flight task → REFUSE.** A patch
    op that mutates or removes a task already running (`wip`) or finished
    (`done`) in the live orchestrator/`_state.json` is refused. You cannot
    surgically rewrite the part of the task graph the chain driver is already
    executing — that is a wholesale re-derivation (`--reopen` territory after a
    deliberate teardown), never a surgical patch.

  * **task add → DELTA-SYNC permitted.** A patch op that adds a genuinely NEW
    task (an id absent from the live orchestrator) is permitted and produces an
    orchestrator *delta* (the single new task object) plus a state *seed* (the
    new id → `ready`). The caller (T5b state-writer resync / T5d dispatch)
    applies the delta; this module only DECIDES + DESCRIBES it.

  * **other op kinds (success_criterion / component / reference / non_goal /
    scalar) → PERMIT WITH ADVISORY.** A non-task edit cannot desync the task
    DAG, so it is allowed — but because the plan is active, an *advisory* is
    surfaced (the operator amended a plan that is already being executed).

Design contract — this is a **pure decision function**, not an in-place
mutator (per the Phase-2 conservatism mandate):

  * It NEVER writes `_state.json` / `_orchestrator_log.jsonl` / the orchestrator
    JSON. It takes the patch op-list + the live orchestrator + the live
    (normalized) state and RETURNS a `ReconciliationPlan` describing the
    per-op decisions and the deltas/seeds an actor should apply.

  * A REFUSE decision on ANY op makes the whole `ReconciliationPlan` refusing
    (`is_refused == True`). The caller MUST treat a refusing plan as
    all-or-nothing: apply NOTHING (no partial sync, no partial state write).
    This is what keeps a refusal CLEAN — the decision is computed BEFORE any
    side effect, so a live `_state.json`/`_orchestrator_log.jsonl` is never
    half-mutated.

  * It is deterministic: ops are evaluated in array order (the same order
    `patch_apply.apply_patch` applies them and the audit log records them), and
    the same inputs always yield the same plan.

T5b builds the status-preserving state resync on `task_seeds`; T5c builds the
`_orchestrator_log.jsonl` append-only continuity; T5d converts the Phase-1
cascade-bypass in `bin/_planner/main.py` into a call THROUGH this reconciler
(running the sync path on an active, NOT-closed plan; denying on a CLOSED one).
So the public surface here is intentionally callable from `main.py`'s amend
dispatch with the inputs that site already has (the validated patch + the slug
dir).

Op-classification reuses `patch_apply`'s op-kind machinery (the
`op_kind`/`action` keys the schema already constrains); "is this task live /
in-flight?" is read from the orchestrator `tasks` array + the `_state.json`
per-task status via the canonical `_orchestrator_query` loaders — the same
status-resolution the junction-naive `compute_ready_set` uses, so this module
and the picker agree on what "wip"/"done" means.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from bin._planner.patch_apply import TASK_OP_KIND, OpSignature, classify_op

__all__ = [
    "ReconcileDecision",
    "OpReconciliation",
    "ReconciliationPlan",
    "ReconciliationRefused",
    "IN_FLIGHT_STATUSES",
    "reconcile_patch",
    "reconcile_active_plan",
    "sync_reconciliation_to_orchestrator",
]


# ---------------------------------------------------------------------------
# In-flight status set. A task whose effective status is in this set has work
# the chain driver has already started (`wip`) or completed (`done`); a patch
# that mutates or removes it is refused (SC5: "task replace/task remove on an
# in-progress (wip) or done orchestrator task REFUSES"). A task that is still
# `ready` (or any not-yet-started status) is NOT in this set: it has not been
# picked up, so the dangerous-rewrite condition has not yet arisen. The refusal
# predicate is deliberately the *narrow* SC5 set (wip + done), not "any task
# present in the orchestrator" — a not-yet-started `ready` task mutation does
# not corrupt in-flight execution and is left to the permit/advisory side.
# ---------------------------------------------------------------------------
IN_FLIGHT_STATUSES: frozenset[str] = frozenset({"wip", "done"})

# Default effective status for a task present in the orchestrator but absent
# from `_state.json` (mirrors `_orchestrator_query.query.DEFAULT_STATUS` and
# `state_writer.get_task_status` returning None → treated as `ready`).
_DEFAULT_STATUS: str = "ready"


class ReconcileDecision(str, Enum):
    """The reconciliation verdict for a single patch op against a live plan.

    * ``PERMIT`` — applied with no caveat (today only the task-ADD path uses a
      bare permit-shaped decision; it carries a delta — see
      ``DELTA_SYNC``). Reserved for completeness / future op kinds.
    * ``PERMIT_WITH_ADVISORY`` — applied, but an advisory is surfaced because
      the plan is active (non-task edits: success_criterion / component /
      reference / non_goal / scalar).
    * ``DELTA_SYNC`` — a task ADD of a new id: permitted, and it emits an
      orchestrator task delta + a `ready` state seed.
    * ``REFUSE`` — a task replace/remove of an in-flight (`wip`/`done`) task:
      loud-refused; makes the whole `ReconciliationPlan` refusing.
    """

    PERMIT = "permit"
    PERMIT_WITH_ADVISORY = "permit_with_advisory"
    DELTA_SYNC = "delta_sync"
    REFUSE = "refuse"


@dataclass(frozen=True)
class OpReconciliation:
    """The reconciliation outcome for ONE patch op (1:1 with the op-list).

    Attributes
    ----------
    op_index : int
        Position of the op in the patch's `ops` array (array order is the
        determinism contract shared with `patch_apply` + the audit log).
    op_kind : str
        The op's `op_kind` (success_criterion / task / component / reference /
        non_goal / scalar).
    action : str
        The op's `action` (replace / add / remove).
    decision : ReconcileDecision
        The verdict for this op.
    target_id : str or None
        For a task op, the addressed task id (`address.id`); None for non-task
        op kinds (their addressing is irrelevant to the task-DAG policy).
    effective_status : str or None
        For a REFUSE on a task op, the in-flight task's resolved status
        (`wip`/`done`) — the reason it was refused. None otherwise.
    advisory : str or None
        Human-readable advisory string for PERMIT_WITH_ADVISORY; None
        otherwise.
    reason : str or None
        Human-readable refusal reason for REFUSE; None otherwise.
    task_delta : dict or None
        For a DELTA_SYNC, the new task OBJECT to insert into the orchestrator
        `tasks` array (a copy of the op's `value`). None otherwise.
    seed_status : str or None
        For a DELTA_SYNC, the status to seed the new id at in `_state.json`
        (always `ready`). None otherwise.
    """

    op_index: int
    op_kind: str
    action: str
    decision: ReconcileDecision
    target_id: Optional[str] = None
    effective_status: Optional[str] = None
    advisory: Optional[str] = None
    reason: Optional[str] = None
    task_delta: Optional[dict] = None
    seed_status: Optional[str] = None

    @property
    def is_refused(self) -> bool:
        return self.decision is ReconcileDecision.REFUSE


@dataclass(frozen=True)
class ReconciliationPlan:
    """The whole-patch reconciliation decision (one `OpReconciliation` per op).

    This is the public return shape of `reconcile_patch` / `reconcile_active
    _plan`. It is a DESCRIPTION, not an applied side effect: the caller acts on
    it (applies deltas, seeds state, logs advisories) only when it is NOT
    refusing.

    All-or-nothing contract: if `is_refused` is True (ANY op refused), the
    caller MUST apply NOTHING — no partial orchestrator delta, no partial state
    seed. The deltas/seeds aggregated here are valid to apply ONLY when
    `is_refused` is False.
    """

    ops: list[OpReconciliation] = field(default_factory=list)

    @property
    def is_refused(self) -> bool:
        """True iff ANY op was refused (whole plan is all-or-nothing)."""
        return any(op.is_refused for op in self.ops)

    @property
    def refused_ops(self) -> list[OpReconciliation]:
        return [op for op in self.ops if op.is_refused]

    @property
    def advisories(self) -> list[str]:
        """Advisory strings (in op-order) for every PERMIT_WITH_ADVISORY op."""
        return [op.advisory for op in self.ops if op.advisory is not None]

    @property
    def task_deltas(self) -> list[dict]:
        """New task objects to insert into the orchestrator `tasks` array.

        Aggregated in op-order. Valid to apply ONLY when `is_refused` is False
        (a refusing plan applies nothing).
        """
        return [op.task_delta for op in self.ops if op.task_delta is not None]

    @property
    def task_seeds(self) -> list[tuple[str, str]]:
        """(`task_id`, `seed_status`) pairs to seed into `_state.json`.

        One per DELTA_SYNC op (status is always `ready`). Consumed by T5b's
        status-preserving state resync. Valid to apply ONLY when `is_refused`
        is False.
        """
        return [
            (op.target_id, op.seed_status)
            for op in self.ops
            if op.seed_status is not None and op.target_id is not None
        ]

    def raise_if_refused(self) -> None:
        """Raise `ReconciliationRefused` if the plan refuses; else no-op.

        Convenience for a caller (T5d dispatch) that wants the refusal to
        surface as an exception rather than branch on `is_refused`. The
        exception carries the refused ops for the stderr envelope.
        """
        if self.is_refused:
            raise ReconciliationRefused(self.refused_ops)


@dataclass
class ReconciliationRefused(Exception):
    """At least one patch op cannot reconcile against the live plan — REFUSED.

    Loud-refusal (the dangerous case is never a soft flag, per SC5). Carries
    the refused `OpReconciliation`s so the CLI dispatch can render which task(s)
    and why. A refusing plan means NOTHING was applied (the decision is computed
    before any side effect), so no partial sync ever occurred.
    """

    refused_ops: list[OpReconciliation] = field(default_factory=list)

    def __str__(self) -> str:
        if not self.refused_ops:
            return "reconciliation refused"
        bits = []
        for op in self.refused_ops:
            bits.append(
                f"op[{op.op_index}] ({op.op_kind}/{op.action}) on task "
                f"{op.target_id!r} (status={op.effective_status!r})"
            )
        joined = "; ".join(bits)
        return (
            f"amend refused: cannot reconcile {len(self.refused_ops)} op(s) "
            f"against the in-flight orchestrator: {joined}. A task that is "
            f"already running or done cannot be surgically replaced or removed "
            f"on an active plan — tear the orchestrator down and re-derive via "
            f"`bin/plan --reopen` if the in-flight task graph must change."
        )


# ---------------------------------------------------------------------------
# Live-status resolution. The "which ids are live + what is each one's status?"
# read is OWNED by `_orchestrator_query.query.live_task_status_map` (T5a wired
# it there per the file_paths_touched contract) so this policy and the picker's
# `compute_ready_set` single-source the `(orchestrator tasks) × (_state.json
# status)` join and agree on wip/done. This module consumes that map; it does
# not re-derive the join.
# ---------------------------------------------------------------------------


def _effective_status(status_map: dict[str, str], task_id: Optional[str]) -> str:
    """Effective status for `task_id` from the precomputed live-status map.

    An id absent from the map (not in the live DAG) degrades to
    `_DEFAULT_STATUS` (`ready`) — consistent with the picker treating an
    unknown id as default-`ready`. `None` (a task op missing its `address.id`)
    also degrades to the default.
    """
    if task_id is None:
        return _DEFAULT_STATUS
    return status_map.get(task_id, _DEFAULT_STATUS)


# ---------------------------------------------------------------------------
# Per-op classification → decision.
# ---------------------------------------------------------------------------


def _task_address_id(sig: OpSignature) -> Optional[str]:
    """The addressed task id for a task op (`address.id`), or None."""
    tid = sig.address.get("id")
    return tid if isinstance(tid, str) and tid else None


def _reconcile_task_op(
    sig: OpSignature,
    op: dict,
    *,
    status_map: dict[str, str],
) -> OpReconciliation:
    """Decide a single `task` op against the live DAG.

    `status_map` is the precomputed `live_task_status_map` (id → effective
    status); its KEYS are exactly the ids in the live orchestrator DAG.

    * ``add`` of a NEW id (absent from the orchestrator) → DELTA_SYNC (emit the
      new task object + seed it `ready`). An ``add`` whose id ALREADY exists in
      the orchestrator is a collision — refused (you cannot "add" a task the DAG
      already has; the surgical apply engine would also reject the colliding add
      at plan level, but the DAG-level collision is the reconciliation concern
      here).
    * ``replace`` / ``remove`` of an in-flight (`wip`/`done`) task → REFUSE.
    * ``replace`` / ``remove`` of a not-in-flight task (ready / absent) → permit
      with advisory (it mutates the plan-side task entry but does not disturb
      in-flight execution; it is surfaced as an advisory like other non-DAG-
      dangerous edits).
    """
    op_index = sig.op_index
    action = sig.action
    target_id = _task_address_id(sig)
    in_live_dag = target_id is not None and target_id in status_map

    if action == "add":
        # A genuinely new task id → delta-sync. A colliding id (already in the
        # DAG) is refused: "adding" a task that already exists would either
        # duplicate or silently overwrite an in-flight node.
        if in_live_dag:
            return OpReconciliation(
                op_index=op_index,
                op_kind=TASK_OP_KIND,
                action=action,
                decision=ReconcileDecision.REFUSE,
                target_id=target_id,
                effective_status=_effective_status(status_map, target_id),
                reason=(
                    f"task add for id {target_id!r} collides with an existing "
                    f"orchestrator task; an add must introduce a NEW id"
                ),
            )
        value = op.get("value")
        task_delta = dict(value) if isinstance(value, dict) else None
        return OpReconciliation(
            op_index=op_index,
            op_kind=TASK_OP_KIND,
            action=action,
            decision=ReconcileDecision.DELTA_SYNC,
            target_id=target_id,
            task_delta=task_delta,
            seed_status=_DEFAULT_STATUS,
        )

    # replace / remove: dangerous only if the task is in-flight.
    if action in ("replace", "remove"):
        if in_live_dag:
            status = _effective_status(status_map, target_id)
            if status in IN_FLIGHT_STATUSES:
                return OpReconciliation(
                    op_index=op_index,
                    op_kind=TASK_OP_KIND,
                    action=action,
                    decision=ReconcileDecision.REFUSE,
                    target_id=target_id,
                    effective_status=status,
                    reason=(
                        f"task {action} on in-flight task {target_id!r} "
                        f"(status={status}) is refused on an active plan"
                    ),
                )
            # Present in the DAG but not in-flight (e.g. `ready`): a plan-side
            # mutation that does not disturb running work. Permit-with-advisory.
            return OpReconciliation(
                op_index=op_index,
                op_kind=TASK_OP_KIND,
                action=action,
                decision=ReconcileDecision.PERMIT_WITH_ADVISORY,
                target_id=target_id,
                effective_status=status,
                advisory=(
                    f"task {action} on not-yet-started task {target_id!r} "
                    f"(status={status}) amended on an active plan; the "
                    f"orchestrator DAG entry is unchanged by --amend (the plan "
                    f"and orchestrator may now differ for this id until a "
                    f"re-sync)"
                ),
            )
        # Target not in the live DAG at all: the plan-side op addresses a task
        # the orchestrator never had (or that a prior delta has not yet synced).
        # Permit-with-advisory — there is no in-flight node to endanger.
        return OpReconciliation(
            op_index=op_index,
            op_kind=TASK_OP_KIND,
            action=action,
            decision=ReconcileDecision.PERMIT_WITH_ADVISORY,
            target_id=target_id,
            advisory=(
                f"task {action} on id {target_id!r} not present in the live "
                f"orchestrator; permitted on the plan side with no DAG impact"
            ),
        )

    # Unknown action (schema bars it upstream); be conservative and refuse.
    return OpReconciliation(  # pragma: no cover - schema-bared action
        op_index=op_index,
        op_kind=TASK_OP_KIND,
        action=str(action),
        decision=ReconcileDecision.REFUSE,
        target_id=target_id,
        reason=f"unknown task action {action!r}",
    )


def _reconcile_non_task_op(sig: OpSignature) -> OpReconciliation:
    """Decide a single NON-task op: permit, but advise (the plan is active).

    A success_criterion / component / reference / non_goal / scalar edit cannot
    desync the task DAG, so it is always permitted on an active plan — but
    because the plan is being executed, an advisory is surfaced so the operator
    knows an in-flight plan's prose/criteria changed underneath the running
    orchestrator.
    """
    op_kind = sig.op_kind
    action = sig.action
    return OpReconciliation(
        op_index=sig.op_index,
        op_kind=op_kind,
        action=action,
        decision=ReconcileDecision.PERMIT_WITH_ADVISORY,
        advisory=(
            f"{op_kind} {action} amended on an ACTIVE plan (orchestrator "
            f"exists, not closed); permitted — a non-task edit does not change "
            f"the in-flight task DAG, but the plan now differs from the plan "
            f"the orchestrator was derived from"
        ),
    )


def reconcile_patch(
    patch: dict,
    orchestrator: dict,
    state: dict,
) -> ReconciliationPlan:
    """Decide, per op, how a patch reconciles against a live orchestrator/state.

    This is the **pure** core of the T5a policy: it reads (never writes) the
    inputs and returns a `ReconciliationPlan` describing the per-op decisions +
    the orchestrator deltas / state seeds an actor should apply when the plan is
    not refusing.

    Parameters
    ----------
    patch : dict
        A `plan_patch_v1`-validated object (`{"patch_version": 1, "ops": [...]}`)
        — the SAME object `patch_apply.apply_patch` consumes. Only `op_kind`,
        `action`, `address`, and (for task adds) `value` are read.
    orchestrator : dict
        The parsed `<slug>_orchestrator.json` (live task DAG). Only its `tasks`
        array (each `id`) is read — the junction-naive contract is untouched.
    state : dict
        The NORMALIZED `_state.json` (canonical `tasks` dict keyed by id, per
        `_orchestrator_query.state_loader.load_state`). Per-task `status` is
        read to resolve wip/done.

    Returns
    -------
    ReconciliationPlan
        One `OpReconciliation` per op (array order preserved). `is_refused` is
        True iff any op refused; when True the caller applies NOTHING.

    Notes
    -----
    Deterministic + side-effect-free. Ops are evaluated in `patch["ops"]` array
    order (the determinism contract shared with the apply engine + the audit
    log). The input dicts are never mutated; task deltas are COPIES of the op
    `value`s so a later orchestrator write cannot reach back into the patch.
    """
    # Single-source the live-DAG membership + per-id status from the picker's
    # reader (T5a wired it into `query.py`); the map KEYS are the live ids.
    from bin._orchestrator_query.query import live_task_status_map

    ops = patch.get("ops") or []
    status_map = live_task_status_map(orchestrator, state)

    decisions: list[OpReconciliation] = []
    for op_index, op in enumerate(ops):
        # `classify_op` is the single op-kind/action reader (owned by
        # patch_apply); it raises loudly on a malformed/foreign op rather than
        # silently mis-routing. The reconciler runs AFTER plan_patch_v1 schema
        # validation upstream, so a raise here is a defensive last line.
        sig = classify_op(op, op_index)
        if sig.is_task:
            decisions.append(
                _reconcile_task_op(sig, op, status_map=status_map)
            )
        else:
            decisions.append(_reconcile_non_task_op(sig))

    return ReconciliationPlan(ops=decisions)


def reconcile_active_plan(
    patch: dict,
    plan_dir: Path,
    slug: str,
) -> ReconciliationPlan:
    """Filesystem-facing entry point: load the live orchestrator + state, then
    `reconcile_patch`.

    This is the surface `bin/_planner/main.py`'s amend dispatch (T5d) calls when
    an `--amend` lands on an ACTIVE (orchestrator-exists, NOT-closed) plan. It
    is a thin I/O wrapper around the pure `reconcile_patch`:

      1. Load `<slug>_orchestrator.json` via the canonical orchestrator loader.
      2. Load + normalize `_state.json` via the canonical state loader.
      3. Delegate to `reconcile_patch`.

    Parameters
    ----------
    patch : dict
        The `plan_patch_v1`-validated patch (see `reconcile_patch`).
    plan_dir : Path
        `docs/plans/<slug>/` directory.
    slug : str
        The plan slug (used to resolve `<slug>_orchestrator.json`).

    Returns
    -------
    ReconciliationPlan
        Same shape as `reconcile_patch`.

    Raises
    ------
    Propagates the loader exceptions
    (`OrchestratorJsonMissingError` / `OrchestratorJsonMalformedError` /
    `SlugNotFoundError` from `orchestrator_loader`; `StateShapeInvalidError`
    from `state_loader`) UNCHANGED — the caller (T5d) decides how to map them.
    This wrapper does NOT swallow them: an unreadable live DAG must not silently
    fall through to a permissive sync.

    Notes
    -----
    Read-only: this loads but never writes the orchestrator or `_state.json`.
    The actual delta/seed application is the caller's job (T5b/T5d), gated on a
    non-refusing `ReconciliationPlan`.
    """
    # Imported here (not at module top) so the pure `reconcile_patch` core stays
    # importable without dragging in the orchestrator-query I/O surface — keeps
    # unit tests of the policy logic dependency-light.
    from bin._orchestrator_query.orchestrator_loader import load_orchestrator
    from bin._orchestrator_query.state_loader import load_state

    plan_dir = Path(plan_dir)
    orchestrator = load_orchestrator(plan_dir, slug)
    state = load_state(plan_dir)
    return reconcile_patch(patch, orchestrator, state)


def sync_reconciliation_to_orchestrator(
    plan: ReconciliationPlan,
    plan_dir: Path,
    slug: str,
    *,
    reason: Optional[str] = None,
) -> list[str]:
    """Apply a NON-refusing `ReconciliationPlan`'s task deltas to the live state,
    recording an append-only `_orchestrator_log.jsonl` continuity row (T5c).

    This is the thin I/O ORCHESTRATION seam between the PURE reconciliation
    decision (`reconcile_patch`, which never writes) and the side-effecting
    `_state.json` + `_orchestrator_log.jsonl` writers that own all state I/O.
    `reconcile.py`'s pure core stays pure: the actual WRITE lives entirely in
    `bin/_update_orchestrator/state_writer.resync_with_continuity` (the
    sole-`_state.json`-writer-under-flock + the registered append-only log
    emitter). This function only:

      1. Enforces the all-or-nothing refusal contract (`raise_if_refused`): a
         refusing plan applies NOTHING — no state seed, no continuity row.
      2. Extracts the `task_seeds` the reconciler already computed (the new
         ids → `ready`).
      3. Delegates to the T5c compound writer, which under ONE held state flock
         resyncs `_state.json` (T5b, status-preserving) AND appends the
         `orchestrator_resync` continuity row through the same append-only
         (`"ab"`-mode) writer every transition row uses — so prior log rows are
         NEVER truncated, rewritten, or reordered.

    Per the T5c contract, the log WRITE belongs to the `bin/_update_orchestrator/`
    writer, not here: this module contributes only the glue that turns a decided
    `ReconciliationPlan` into the writer's inputs. The continuity row is emitted
    even when `task_seeds` is empty (e.g. an active-plan amend that only
    permitted non-task edits) — the resync event itself is the recordable fact.

    Parameters
    ----------
    plan : ReconciliationPlan
        The decision returned by `reconcile_patch` / `reconcile_active_plan`.
        MUST NOT be refusing (this raises `ReconciliationRefused` if it is, the
        same exception `raise_if_refused` raises, so the caller has a single
        refusal type to handle).
    plan_dir : Path
        `docs/plans/<slug>/` directory.
    slug : str
        Plan slug (stamped on the continuity row's `plan_slug`).
    reason : str, optional
        Free-form reason override for the continuity row.

    Returns
    -------
    list[str]
        The ids genuinely ADDED to `_state.json` by the resync (per the
        status-preserving merge; survivors are never re-seeded).

    Raises
    ------
    ReconciliationRefused
        If `plan.is_refused` — applied NOTHING (no state write, no log row).
    """
    # All-or-nothing: a refusing plan must apply nothing. Raising here keeps the
    # "decision computed BEFORE any side effect" guarantee — no `_state.json` and
    # no `_orchestrator_log.jsonl` row is ever written for a refused plan.
    plan.raise_if_refused()

    # Imported here (not at module top) so the pure decision core
    # (`reconcile_patch`) stays importable without the `_state.json` /
    # `_orchestrator_log.jsonl` writer surface — matches `reconcile_active_plan`'s
    # lazy-import discipline.
    from bin._update_orchestrator.state_writer import resync_with_continuity

    return resync_with_continuity(
        Path(plan_dir),
        plan.task_seeds,
        slug=slug,
        reason=reason,
    )
