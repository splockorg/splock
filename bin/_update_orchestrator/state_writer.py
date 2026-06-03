"""`_state.json` atomic reader/writer for `bin/update_orchestrator` (implplan §E.impl).

Delegates atomic-write to the shared `bin/_render_plan/atomic_write.py`
helper per cross-cutting conventions lines 280-284. Read-modify-write
under flock per cross-cutting `flock discipline` lines 286-290.

The `_state.json` file is the canonical task-state store; this module
mutates it on every CLI invocation.

Compound-write lock order (per `bin/_jsonl_log/writer.py` docstring):
1. Acquire `_state.json.lock` FIRST (handled here).
2. THEN call `append_row(...)` (which internally acquires
   `_orchestrator_log.jsonl.lock`).
3. Inside the inner critical section, the `_state.json` write happens
   BEFORE `append_row` returns.

The functions here expose the pattern; the chosen order is the caller's
responsibility (see `from_develop_plan.py::apply_status_change`).
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Optional, Tuple

from bin._render_plan.atomic_write import write_atomic


SEVEN_STATUS = frozenset(
    {"ready", "wip", "done", "deferred", "blocked", "cancelled", "unknown"}
)


def state_path(plan_dir: Path) -> Path:
    return Path(plan_dir) / "_state.json"


def state_lock_path(plan_dir: Path) -> Path:
    return Path(plan_dir) / "_state.json.lock"


@contextmanager
def state_flock(plan_dir: Path) -> Iterator[None]:
    """Acquire exclusive flock on `_state.json.lock` for the full RMW cycle.

    The lockfile is created if missing. The lock is released when the
    context exits.
    """
    lock_path = state_lock_path(plan_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # `os.open` with O_CREAT so concurrent first-callers race-safe create.
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def read_state(plan_dir: Path) -> dict:
    """Read `_state.json`; return `{}` if absent."""
    path = state_path(plan_dir)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_state(plan_dir: Path, state: dict) -> None:
    """Atomic write of `_state.json` via the shared helper.

    Caller MUST hold `state_flock(plan_dir)` for the RMW cycle.
    """
    path = state_path(plan_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    write_atomic(path, payload)


def get_task_entry(state: dict, task_id: str) -> dict:
    """Return the task entry for `task_id`, creating an empty dict if missing.

    Mutates `state` if the entry is created. The shape of the task entry
    is open (other sections populate fields like `status`, `retry_count`,
    `develop_plan_telemetry`); this helper only ensures the slot exists.
    """
    tasks = state.setdefault("tasks", {})
    return tasks.setdefault(task_id, {})


def get_task_status(state: dict, task_id: str) -> Optional[str]:
    tasks = state.get("tasks") or {}
    entry = tasks.get(task_id)
    if not isinstance(entry, dict):
        return None
    status = entry.get("status")
    return status if isinstance(status, str) else None


def set_task_status(state: dict, task_id: str, status: str) -> None:
    """Set the canonical status on a task entry.

    Raises `ValueError` if `status` is not in the 7-status enum.
    """
    if status not in SEVEN_STATUS:
        raise ValueError(
            f"status={status!r} not in 7-status enum; valid: {sorted(SEVEN_STATUS)}"
        )
    entry = get_task_entry(state, task_id)
    entry["status"] = status


def detect_wip_set_empty(state: dict) -> bool:
    """Return True iff no task in `state.tasks` has `status == 'wip'`.

    Used by §A.impl.7 path 2 (completion-summary on wip-set-empty);
    consumed here so the chain-driver-side caller can dispatch.
    """
    tasks = state.get("tasks") or {}
    if not isinstance(tasks, dict):
        return True
    for entry in tasks.values():
        if isinstance(entry, dict) and entry.get("status") == "wip":
            return False
    return True


# --------------------------------------------------------------------------- #
# Status-preserving delta resync (plan_surgical_amend T5b / §SC5).
#
# When an `--amend` delta-syncs genuinely NEW tasks into an ALREADY-ACTIVE
# orchestrator (T5a's `DELTA_SYNC` decision), `_state.json` must gain the new
# task ids — and ONLY the new ids — without disturbing any task the chain driver
# has already touched. The reconciler (`bin/_planner/reconcile.py`) emits its
# `ReconciliationPlan.task_seeds` as `(task_id, "ready")` pairs and — by its
# deliberate contract — emits a seed ONLY for a new id (it never re-emits a
# survivor). So this resync receives ONLY the deltas; its job is to add each new
# id at its seed status while leaving EVERY existing entry byte-for-byte intact.
#
# The invariant (status-preservation): an existing id's entry — its `status` and
# every other field (`retry_count`, `develop_plan_telemetry`, ...) — is NEVER
# read-modified-written. A `wip` stays `wip`, a `done` stays `done`, a `ready`
# stays `ready`. A seed for an id that ALREADY exists is a NO-OP (the survivor
# wins): the resync never re-defaults, resets, or clobbers a present task. This
# is the additive-only, conservative posture the Phase-2 mandate requires — a
# resync can only ADD ids, never mutate or drop them.
# --------------------------------------------------------------------------- #


def merge_seeds_preserving_status(
    state: dict,
    task_seeds: Iterable[Tuple[str, str]],
) -> list[str]:
    """Merge new-task `(id, status)` seeds into `state` IN PLACE, preserving survivors.

    For each `(task_id, seed_status)` seed:

    * if `task_id` is ALREADY present in `state["tasks"]` → NO-OP. The existing
      entry (status + all other fields) is left untouched. The survivor always
      wins; a seed never resets/re-defaults/clobbers a present task. This is the
      status-preservation invariant.
    * if `task_id` is ABSENT → a new entry `{"status": seed_status}` is created.

    Pure dict mutation (no I/O, no flock) so the merge logic is unit-testable in
    isolation and the production resync + any future caller share the exact same
    semantics. The caller is responsible for the flock + atomic write.

    Parameters
    ----------
    state : dict
        A parsed `_state.json` dict. `state["tasks"]` is the dict-keyed-by-id
        store (the CURRENT shape per `get_task_entry`; the array-shape migration
        is out of scope here — marker SSM.1). Created as `{}` if absent.
    task_seeds : iterable of (str, str)
        New-task seeds, exactly `ReconciliationPlan.task_seeds`'s shape
        (`(task_id, seed_status)`; the reconciler always emits `"ready"`). Each
        `seed_status` is validated against the 7-status enum so a malformed seed
        fails loudly rather than persisting an out-of-enum status.

    Returns
    -------
    list[str]
        The ids that were genuinely ADDED (in seed order, de-duplicated),
        excluding any seed whose id already existed (a no-op survivor). Lets the
        caller log/audit exactly what the resync introduced.

    Raises
    ------
    ValueError
        If any `seed_status` is not in the 7-status enum (mirrors
        `set_task_status`'s validation — a resync must not write a status the
        canonical store forbids).
    """
    tasks = state.setdefault("tasks", {})
    added: list[str] = []
    for task_id, seed_status in task_seeds:
        if seed_status not in SEVEN_STATUS:
            raise ValueError(
                f"seed status={seed_status!r} for task {task_id!r} not in "
                f"7-status enum; valid: {sorted(SEVEN_STATUS)}"
            )
        if task_id in tasks:
            # Survivor: leave the existing entry (status + all fields) verbatim.
            # The status-preservation invariant — never re-default a present id.
            continue
        tasks[task_id] = {"status": seed_status}
        added.append(task_id)
    return added


def resync_state_preserving_status(
    plan_dir: Path,
    task_seeds: Iterable[Tuple[str, str]],
    *,
    assume_flock_held: bool = False,
) -> list[str]:
    """Resync `_state.json` with new-task `task_seeds`, preserving every survivor.

    The filesystem-facing T5b entry point the amend reconcile path (T5d
    dispatch) invokes after `reconcile_active_plan` returns a NON-refusing
    `ReconciliationPlan`. It performs the full read-modify-write of `_state.json`
    under flock, adding the new ids at their seed status (`ready`) while leaving
    EVERY existing task's status — and all other fields — byte-for-byte intact.

    Atomicity: the write goes through the shared `write_atomic` helper (mirrors
    `write_state`), so `_state.json` is never left partial/truncated even if the
    process dies mid-write. The read-modify-write happens inside the
    `state_flock` critical section (the sole-writer-under-flock contract this
    module owns) so a concurrent CLI status-transition cannot interleave.

    Parameters
    ----------
    plan_dir : Path
        `docs/plans/<slug>/` directory.
    task_seeds : iterable of (str, str)
        `ReconciliationPlan.task_seeds` — `(task_id, seed_status)` pairs for the
        genuinely-new tasks the amend added (the reconciler never emits a
        survivor here, so every seed is a delta).
    assume_flock_held : bool
        When True, the caller already holds `state_flock(plan_dir)`; the RMW runs
        WITHOUT re-acquiring (avoids self-deadlock when this is invoked from
        inside an existing flock, e.g. a compound amend-sync). When False
        (default), the flock is acquired here for the full cycle.

    Returns
    -------
    list[str]
        The ids actually ADDED (per `merge_seeds_preserving_status`).

    Raises
    ------
    ValueError
        Propagated from `merge_seeds_preserving_status` for an out-of-enum seed
        status — and crucially, the loud failure happens BEFORE any write, so an
        invalid seed never produces a partial `_state.json`.
    """
    seeds = list(task_seeds)

    def _do() -> list[str]:
        state = read_state(plan_dir)
        # Validation runs first (inside merge); on a bad seed it raises before
        # `write_state`, so a refused resync never half-writes the file.
        added = merge_seeds_preserving_status(state, seeds)
        write_state(plan_dir, state)
        return added

    if assume_flock_held:
        return _do()
    with state_flock(plan_dir):
        return _do()


# --------------------------------------------------------------------------- #
# Amend-resync log continuity (plan_surgical_amend T5c / §SC5).
#
# When an `--amend` delta-syncs new tasks into an ALREADY-ACTIVE orchestrator
# (T5a's `DELTA_SYNC` decision + T5b's `_state.json` resync), the
# `_orchestrator_log.jsonl` forensic record must gain a CONTINUITY ROW that
# records the sync event — WITHOUT ever truncating, rewriting, or reordering a
# single prior row. The log stays a complete, append-only history across the
# amend-driven resync.
#
# The continuity row is emitted through the SAME append-only writer every other
# transition row uses: `bin/_update_orchestrator/log_emit.emit_transition` →
# `bin/_jsonl_log/writer.append_row`, whose `_append_one_row_unlocked` opens the
# JSONL in `"ab"` (binary-APPEND) mode under the `_orchestrator_log.jsonl.lock`
# flock and never `"w"`. So append-only-ness is a structural property of the
# writer, not something this layer re-implements — T5c only ADDS one more
# registered-emitter call. The row matches the standard StandardRow shape
# (`emitted_by` / `plan_slug` / `reason` / `schema_version` / `task_id` /
# `transition` / `ts` / `writer_host` / `writer_pid`) plus an
# `orchestrator_resync` `event_type` payload discriminator + the synced ids, so
# morning-review / forensic greps can isolate the amend-resync events.
#
# `task_id` is None and `transition` is `unknown → unknown`: this is an
# OBSERVABILITY row recording a multi-task sync event, not a single task's
# status transition (the new ids' actual `ready` seeding lives in `_state.json`,
# written by `resync_state_preserving_status`; this row is the audit breadcrumb
# that the sync happened). The §C row schema permits `unknown` on both
# transition ends and a `null` `task_id`, so the row validates cleanly.
# --------------------------------------------------------------------------- #

RESYNC_EVENT_TYPE = "orchestrator_resync"
"""`event_type` payload discriminator stamped on the amend-resync continuity
row (free-form per the §C additive-payload philosophy; not a closed-enum
surface on the row schema)."""


def emit_resync_continuity(
    plan_dir: Path,
    *,
    slug: str,
    synced_task_ids: Iterable[str],
    reason: Optional[str] = None,
) -> bool:
    """Append ONE `orchestrator_resync` continuity row recording an amend resync.

    The T5c append-only continuity breadcrumb: it records that an `--amend`
    delta-synced `synced_task_ids` into an active orchestrator, while the log's
    prior rows are left byte-for-byte intact (the append-only invariant is the
    writer's, not re-implemented here — see the module-section comment above).

    The row is stamped through the registered `bin/update_orchestrator` writer
    identity via the shared `log_emit.emit_transition` surface, so it goes
    through the SAME `append_row` path (`"ab"`-mode, under
    `_orchestrator_log.jsonl.lock`, with fsync) every other transition row uses.
    No new file handle, no `"w"`/truncate, no direct JSONL open lives here.

    Best-effort: any writer hiccup is SWALLOWED (returns False) so a logging
    failure never fails the resync — the durable artifact is `_state.json`
    (already written by `resync_state_preserving_status`); this row is advisory
    audit. (Mirrors `closed_state._emit_closed_audit`'s best-effort posture.)

    Parameters
    ----------
    plan_dir : Path
        `docs/plans/<slug>/` directory.
    slug : str
        Plan slug (stamped as `plan_slug` on the row).
    synced_task_ids : iterable of str
        The genuinely-new task ids the resync added (exactly the ids
        `resync_state_preserving_status` reports as ADDED). Recorded on the row
        for forensics; an EMPTY iterable still emits a row (the amend ran and
        produced no new ids — a real, recordable event).
    reason : str, optional
        Free-form `reason` for the row. A default is generated from the synced
        ids when omitted.

    Returns
    -------
    bool
        True iff the row was appended; False if the best-effort emit was
        swallowed (writer unavailable / errored).

    Notes
    -----
    Lock scope: `emit_transition` → `append_row` acquires
    `_orchestrator_log.jsonl.lock` (a DIFFERENT lock from `_state.json.lock`).
    Per the compound-write lock-order contract (`bin/_jsonl_log/writer.py`
    docstring: state lock FIRST, then `append_row`), a compound amend-sync should
    perform the `_state.json` resync BEFORE calling this — see
    `resync_with_continuity`, which sequences them correctly under one held
    state flock.
    """
    ids = list(synced_task_ids)
    try:
        from .log_emit import EMIT_BASE, emit_transition

        emit_transition(
            plan_dir=plan_dir,
            plan_slug=slug,
            task_id=None,
            transition_from="unknown",
            transition_to="unknown",
            reason=reason
            or (
                f"orchestrator resync (amend delta-sync): synced "
                f"{len(ids)} new task id(s): {ids}"
                if ids
                else "orchestrator resync (amend delta-sync): no new task ids"
            ),
            emitted_by=EMIT_BASE,
            extra={
                "event_type": RESYNC_EVENT_TYPE,
                "synced_task_ids": ids,
            },
        )
        return True
    except Exception:
        # Best-effort — the resync's durable artifact (`_state.json`) is already
        # written; an advisory continuity row that fails to append must not fail
        # the sync. (Same posture as `closed_state._emit_closed_audit`.)
        return False


def resync_with_continuity(
    plan_dir: Path,
    task_seeds: Iterable[Tuple[str, str]],
    *,
    slug: str,
    reason: Optional[str] = None,
) -> list[str]:
    """Compound amend-sync: resync `_state.json` THEN append the continuity row.

    The single coherent amend-resync step T5d's dispatch invokes after
    `reconcile_active_plan` returns a NON-refusing `ReconciliationPlan`. It runs
    BOTH halves of the resync under ONE held `_state.json` flock, in the order
    the compound-write lock contract mandates:

      1. Acquire `state_flock(plan_dir)` (the `_state.json.lock`) — held for the
         whole compound step.
      2. Resync `_state.json` (seed the new ids `ready`, preserve every
         survivor) via `resync_state_preserving_status(..., assume_flock_held=
         True)` — the T5b seam, reused so we do NOT re-acquire the state lock.
      3. WHILE still holding the state lock, append the `orchestrator_resync`
         continuity row via `emit_resync_continuity` (which internally acquires
         the SEPARATE `_orchestrator_log.jsonl.lock`).

    This is exactly the §C compound-write lock order (state lock FIRST, THEN
    `append_row`): the state mutation is durable BEFORE the breadcrumb row
    persists, and the two locks are always taken in the same order so a
    concurrent CLI status-transition can never deadlock against this path.

    The continuity row records ONLY the ids genuinely added by THIS resync (the
    return value of the state resync), so the forensic record names exactly what
    the sync introduced.

    Parameters
    ----------
    plan_dir : Path
        `docs/plans/<slug>/` directory.
    task_seeds : iterable of (str, str)
        `ReconciliationPlan.task_seeds` — the new-task `(id, "ready")` seeds.
    slug : str
        Plan slug (for the continuity row's `plan_slug`).
    reason : str, optional
        Free-form reason override for the continuity row.

    Returns
    -------
    list[str]
        The ids actually ADDED by the state resync (per
        `resync_state_preserving_status`).

    Raises
    ------
    ValueError
        Propagated from the state resync for an out-of-enum seed status — raised
        BEFORE any state write AND before the continuity row, so a refused resync
        neither half-writes `_state.json` nor appends a misleading row.
    """
    with state_flock(plan_dir):
        added = resync_state_preserving_status(
            plan_dir, task_seeds, assume_flock_held=True
        )
        # State write is durable (flock still held); append the breadcrumb. The
        # continuity row records exactly the ids THIS resync added.
        emit_resync_continuity(
            plan_dir,
            slug=slug,
            synced_task_ids=added,
            reason=reason,
        )
    return added
