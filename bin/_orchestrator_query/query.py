"""Compute the ready-set from orchestrator + state (code_next_ready_pick T1).

Pure functional core; no I/O, no flock, no exit codes. The CLI in
`main.py` orchestrates the I/O surface; this module is import-clean and
can be called from any future in-process Python caller (mirrors the
`render_invoker` precedent from orch_status_render T4).

Algorithm (per plan §F1):

1. For each task in orchestrator order, resolve the effective status
   (state lookup or default `ready`).
2. A task's `depends_on` is "satisfied" when ALL listed deps have
   effective status in `DEPS_SATISFYING = {done, cancelled, deferred}`.
3. A task is "ready" iff its own effective status is `ready` AND its
   deps are satisfied.
4. Sort the ready set: T-prefixed ids first (sorted by numeric suffix),
   then non-T-prefixed ids by declared order (T0 V3/D-tiebreak-regex
   branch decision — schema does not regex-constrain `id`).
5. If the ready set is non-empty, return verdict=`has_ready` and
   `first_ready` = first element of the sorted ready set.
6. Else compute the verdict from the per-task status snapshot:
   - all terminal (done/cancelled/deferred) → `all_done`
   - any blocked, none wip → `all_blocked`
   - any wip, none blocked → `all_wip`
   - mixed → `mixed_no_ready`
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Literal


# Status values that satisfy a dependency edge (plan §F1.2).
DEPS_SATISFYING: frozenset[str] = frozenset({"done", "cancelled", "deferred"})

# Terminal-done-equivalent statuses (used for the `all_done` verdict).
# Same set as DEPS_SATISFYING — the picker treats them identically.
TERMINAL_STATUSES: frozenset[str] = DEPS_SATISFYING

# Default effective status for tasks not present in state.
DEFAULT_STATUS: str = "ready"

Verdict = Literal[
    "has_ready", "all_done", "all_blocked", "all_wip", "mixed_no_ready"
]


@dataclass
class TaskBreakdown:
    """Per-task picker reasoning, surfaced via `--verbose` / `--json`."""

    id: str
    effective_status: str
    depends_on: list[str]
    unsatisfied_deps: list[str]
    is_ready: bool


@dataclass
class ReadySetReport:
    """Output of `compute_ready_set`.

    Fields surface both the picker's answer (`first_ready`) and its
    reasoning trail (per-task breakdown + counts) so `--verbose` and
    `--json` callers can render whatever shape they need.
    """

    ready_task_ids: list[str]
    first_ready: str | None
    verdict: Verdict
    blocked_ids: list[str] = field(default_factory=list)
    wip_ids: list[str] = field(default_factory=list)
    done_ids: list[str] = field(default_factory=list)
    cancelled_ids: list[str] = field(default_factory=list)
    deferred_ids: list[str] = field(default_factory=list)
    unknown_ids: list[str] = field(default_factory=list)
    breakdown: list[TaskBreakdown] = field(default_factory=list)

    def to_jsonable(self) -> dict:
        """Return a JSON-serializable dict (CLI `--json` mode)."""
        return asdict(self)


_T_PREFIX_RE = re.compile(r"^T(\d+)$")


def _sort_key(task_id: str, declared_index: int) -> tuple[int, int, int]:
    """Return a sort key for tiebreak ordering.

    Per T0 V3/D-tiebreak-regex: T-prefixed ids sort by numeric suffix in
    a first namespace; non-T-prefixed ids sort by declared order in a
    second namespace. Both stable under sort.
    """
    m = _T_PREFIX_RE.match(task_id)
    if m is not None:
        return (0, int(m.group(1)), declared_index)
    return (1, 0, declared_index)


def _resolve_status(state_tasks: dict, task_id: str) -> str:
    """Look up effective status; default to `DEFAULT_STATUS` if absent."""
    entry = state_tasks.get(task_id)
    if not isinstance(entry, dict):
        return DEFAULT_STATUS
    status = entry.get("status")
    if not isinstance(status, str) or not status:
        return DEFAULT_STATUS
    return status


def compute_ready_set(orchestrator: dict, state: dict) -> ReadySetReport:
    """Return the ready-set report for `(orchestrator, state)`.

    Parameters
    ----------
    orchestrator : dict
        Parsed `<slug>_orchestrator.json` payload. Must have a `tasks`
        list per the v1 schema. Each task entry must have `id` and may
        have `depends_on`.
    state : dict
        Parsed `_state.json` payload, NORMALIZED to canonical form
        (`tasks` is `dict[id, entry]` per `state_loader.load_state`).

    Returns
    -------
    ReadySetReport
        - `ready_task_ids`: sorted ready ids (T-numeric first, then
          declared-order for non-T).
        - `first_ready`: first element of `ready_task_ids`, or None.
        - `verdict`: closed-enum string (`has_ready`/`all_done`/
          `all_blocked`/`all_wip`/`mixed_no_ready`).
        - Per-status id buckets (for `--verbose` + `--json` callers).
        - `breakdown`: per-task reasoning trail.
    """
    tasks = orchestrator.get("tasks") or []
    state_tasks = state.get("tasks") or {}
    if not isinstance(state_tasks, dict):
        # Defensive: state_loader normalizes to dict; if a caller hands us
        # a non-canonical state, treat all tasks as default-status.
        state_tasks = {}

    # Step 1: resolve per-task effective status + dep-satisfaction.
    breakdown: list[TaskBreakdown] = []
    ready_with_index: list[tuple[str, int]] = []
    blocked_ids: list[str] = []
    wip_ids: list[str] = []
    done_ids: list[str] = []
    cancelled_ids: list[str] = []
    deferred_ids: list[str] = []
    unknown_ids: list[str] = []

    for idx, task in enumerate(tasks):
        if not isinstance(task, dict):
            # Skip malformed task entry; orchestrator_loader's shape
            # check would normally catch this upstream, but be defensive.
            continue
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id:
            continue
        depends_on = task.get("depends_on") or []
        if not isinstance(depends_on, list):
            depends_on = []
        effective_status = _resolve_status(state_tasks, task_id)

        # Bucket the task into a per-status list.
        if effective_status == "blocked":
            blocked_ids.append(task_id)
        elif effective_status == "wip":
            wip_ids.append(task_id)
        elif effective_status == "done":
            done_ids.append(task_id)
        elif effective_status == "cancelled":
            cancelled_ids.append(task_id)
        elif effective_status == "deferred":
            deferred_ids.append(task_id)
        elif effective_status == "ready":
            pass  # handled by ready-check below
        else:
            unknown_ids.append(task_id)

        # Dep satisfaction: every dep must resolve to a DEPS_SATISFYING
        # status. Default-`ready` does NOT satisfy.
        unsatisfied_deps: list[str] = []
        for dep_id in depends_on:
            if not isinstance(dep_id, str):
                continue
            dep_status = _resolve_status(state_tasks, dep_id)
            if dep_status not in DEPS_SATISFYING:
                unsatisfied_deps.append(dep_id)

        is_ready = (
            effective_status == "ready" and not unsatisfied_deps
        )

        if is_ready:
            ready_with_index.append((task_id, idx))

        breakdown.append(
            TaskBreakdown(
                id=task_id,
                effective_status=effective_status,
                depends_on=[d for d in depends_on if isinstance(d, str)],
                unsatisfied_deps=unsatisfied_deps,
                is_ready=is_ready,
            )
        )

    # Step 4: tiebreak sort.
    ready_sorted = sorted(
        ready_with_index, key=lambda pair: _sort_key(pair[0], pair[1])
    )
    ready_task_ids = [pair[0] for pair in ready_sorted]
    first_ready = ready_task_ids[0] if ready_task_ids else None

    # Step 5/6: verdict.
    verdict = _compute_verdict(
        breakdown=breakdown,
        ready_task_ids=ready_task_ids,
        blocked_ids=blocked_ids,
        wip_ids=wip_ids,
    )

    return ReadySetReport(
        ready_task_ids=ready_task_ids,
        first_ready=first_ready,
        verdict=verdict,
        blocked_ids=blocked_ids,
        wip_ids=wip_ids,
        done_ids=done_ids,
        cancelled_ids=cancelled_ids,
        deferred_ids=deferred_ids,
        unknown_ids=unknown_ids,
        breakdown=breakdown,
    )


# ---------------------------------------------------------------------------
# Active-orchestrator live-status read (plan_surgical_amend T5a / §SC5).
#
# The amend reconciler (`bin/_planner/reconcile.py`) needs a single read that
# answers "which task ids are in the live DAG, and what is each one's effective
# status?" — the exact `(orchestrator tasks) × (_state.json status)` join the
# picker already computes, but exposed WITHOUT running the full ready-set /
# verdict machinery. These two helpers are that minimal read. They reuse the
# picker's `_resolve_status` (same default-`ready` semantics) so the reconciler
# and `compute_ready_set` agree on what "wip"/"done" mean. The junction-naive
# `compute_ready_set` contract above is UNTOUCHED — these are additive readers.
# ---------------------------------------------------------------------------


def live_task_ids(orchestrator: dict) -> set[str]:
    """Set of task ids present in the live orchestrator `tasks` array.

    A task is "in the live DAG" iff it appears (with a non-empty string `id`)
    in `orchestrator["tasks"]`. Malformed entries are skipped (defensive —
    `orchestrator_loader` shape-checks `tasks` is a list upstream).
    """
    ids: set[str] = set()
    tasks = orchestrator.get("tasks") or []
    if not isinstance(tasks, list):
        return ids
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = task.get("id")
        if isinstance(task_id, str) and task_id:
            ids.add(task_id)
    return ids


def live_task_status_map(orchestrator: dict, state: dict) -> dict[str, str]:
    """Map every live orchestrator task id → its effective `_state.json` status.

    The reconciler's "is this task in-flight?" read. For each id in the live DAG
    (`orchestrator["tasks"]`), resolve its effective status from the NORMALIZED
    `_state.json` (`state["tasks"]` dict-by-id, per
    `state_loader.load_state`), defaulting to `DEFAULT_STATUS` (`ready`) when
    the id has no state entry or no status — identical to the picker's
    `_resolve_status`. Returns ONLY ids present in the orchestrator: a stale
    state entry for a now-absent id does not appear (the live DAG is the
    membership authority).

    This is a read-only join; neither input is mutated.
    """
    state_tasks = state.get("tasks") or {}
    if not isinstance(state_tasks, dict):
        state_tasks = {}
    return {
        task_id: _resolve_status(state_tasks, task_id)
        for task_id in live_task_ids(orchestrator)
    }


def _compute_verdict(
    *,
    breakdown: list[TaskBreakdown],
    ready_task_ids: list[str],
    blocked_ids: list[str],
    wip_ids: list[str],
) -> Verdict:
    """Decide the verdict per plan §F1.7.

    Order matters: has_ready > all_done > all_blocked > all_wip >
    mixed_no_ready.
    """
    if ready_task_ids:
        return "has_ready"
    # `all_done` requires every task to be in a terminal-done-equivalent
    # status (done/cancelled/deferred). Empty plan also counts as
    # all_done — defensive but matches "nothing to do" semantics.
    if all(b.effective_status in TERMINAL_STATUSES for b in breakdown):
        return "all_done"
    has_blocked = bool(blocked_ids)
    has_wip = bool(wip_ids)
    if has_blocked and not has_wip:
        return "all_blocked"
    if has_wip and not has_blocked:
        return "all_wip"
    return "mixed_no_ready"
