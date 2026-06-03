"""Thin delegate to `bin/_jsonl_log/writer.append_row` (implplan §H.impl + §C.impl).

Per the §C shared-writer discipline: this module exists so tests can mock
`bin._morning_review.log_emit.append_row` to decouple from the real §C
writer, and so `main.py` does not need to import `bin._jsonl_log`
directly.

The `emitted_by` values stamped by this module are §C KNOWN_WRITERS
sub-emitter values (`bin/morning-review:<subcommand>`) — they reflect the
CLI subcommand that ran. The full set is already registered in
`bin/_jsonl_log/writers.py::KNOWN_WRITERS`.

Per implplan §H.impl.3 the seven new `event_type` enum values
(`morning_review_triage_*`, `morning_review_acknowledged`,
`morning_review_index_regenerated`, `morning_review_archived`) flow as
additive payload fields. They are not constrained by the §C row schema
(which has explicit `properties` listings + no `additionalProperties:
false`) and are tolerated by the validator.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import pathlib
from typing import Optional

# Top-level import: real symbol after §C ships; tests mock this symbol.
try:
    from bin._jsonl_log.writer import append_row  # noqa: F401
except ImportError:  # pragma: no cover — only hit during isolated unit-test boot
    def append_row(plan_dir, row, emitted_by):  # type: ignore[no-redef]
        raise RuntimeError(
            "bin/_jsonl_log/writer.py not yet shipped (§C dependency). "
            "Tests should mock `bin._morning_review.log_emit.append_row`."
        )


# Sub-emitter constants (one per CLI subcommand; matches KNOWN_WRITERS).
EMIT_BARE = "bin/morning-review"
EMIT_LIST = "bin/morning-review:list"
EMIT_SHOW = "bin/morning-review:show"
EMIT_REACTIVATE = "bin/morning-review:reactivate"
EMIT_ROUTE_OUTSTANDING = "bin/morning-review:route-outstanding"
EMIT_ROUTE_MARKER = "bin/morning-review:route-marker"
EMIT_ABANDON = "bin/morning-review:abandon"
EMIT_ACKNOWLEDGE = "bin/morning-review:acknowledge"
EMIT_GC = "bin/morning-review:gc"
EMIT_INDEX_REGEN = "bin/morning-review:index-regen"
EMIT_MARK_FOR_EVAL = "bin/morning-review:mark-for-eval"
EMIT_LABEL_SCORE = "bin/morning-review:label-score"
EMIT_RETIRE_CASE = "bin/morning-review:retire-case"


# Event-type closed-enum extensions to §C (additive payload; per §H.impl.3).
EVT_TRIAGE_REACTIVATE = "morning_review_triage_reactivate"
EVT_TRIAGE_ROUTE_OUTSTANDING = "morning_review_triage_route_outstanding"
EVT_TRIAGE_ROUTE_MARKER = "morning_review_triage_route_marker"
EVT_TRIAGE_ABANDON = "morning_review_triage_abandon"
EVT_ACKNOWLEDGED = "morning_review_acknowledged"
EVT_INDEX_REGENERATED = "morning_review_index_regenerated"
EVT_ARCHIVED = "morning_review_archived"


def _session_id() -> str:
    """Read `$CLAUDE_SESSION_ID`, else derive a deterministic placeholder.

    The §C schema constrains `session_id` to pattern `^sess_[0-9a-f]{8}$`;
    if the env var is missing we synthesize a stable per-process slot.
    """
    sid = os.environ.get("CLAUDE_SESSION_ID")
    if sid:
        return sid
    digest = hashlib.sha1(
        f"morning-review|{os.getpid()}".encode("utf-8")
    ).hexdigest()
    return f"sess_{digest[:8]}"


def _now_iso_z() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _chain_id() -> Optional[str]:
    val = os.environ.get("SPLOCK_CHAIN_ID")
    return val if val else None


def _overnight_mode() -> bool:
    return os.environ.get("OVERNIGHT_MODE") == "1"


def emit_triage(
    plan_dir: pathlib.Path,
    *,
    slug: str,
    task_id: str,
    event_type: str,
    sub_emitter: str,
    reason: str,
    pointer: Optional[str] = None,
    transition_to: str = "deferred",
    transition_from: str = "deferred",
) -> None:
    """Append a `morning_review_triage_*` row via the §C shared writer.

    Per §H.impl.6 post-success path step 2: morning-review emits a row
    acknowledging the triage; the underlying CLI emits its own row for
    the canonical state transition.

    Default transition is `deferred → deferred` (the morning-review
    surface row does not change canonical task status — the underlying
    CLI did). The exception is the `reactivate` gesture where the
    underlying `bin/update_orchestrator` emits `deferred → wip`; the
    morning-review acknowledgement row can stay at `deferred → deferred`
    or override via caller-supplied `transition_to`.
    """
    row = {
        "session_id": _session_id(),
        "plan_slug": slug,
        "task_id": task_id,
        "transition": {"from": transition_from, "to": transition_to},
        "mode_at_transition": {
            "overnight": _overnight_mode(),
            "guardrail": False,
        },
        "reason": reason,
        "event_type": event_type,
    }
    cid = _chain_id()
    if cid is not None:
        row["chain_id"] = cid
    if pointer is not None:
        row["pointer"] = pointer
    append_row(plan_dir, row, sub_emitter)


def emit_acknowledged(
    plan_dir: pathlib.Path,
    *,
    slug: str,
) -> None:
    """Append a `morning_review_acknowledged` row.

    Non-terminal gesture per plan §H.4: clears cap-hit banner state but
    does not change task status. The row's `task_id` is null (the
    acknowledgement is slug-scoped, not task-scoped).
    """
    row = {
        "session_id": _session_id(),
        "plan_slug": slug,
        "task_id": None,
        "transition": {"from": "deferred", "to": "deferred"},
        "mode_at_transition": {
            "overnight": _overnight_mode(),
            "guardrail": False,
        },
        "reason": "morning_review_acknowledged",
        "event_type": EVT_ACKNOWLEDGED,
    }
    cid = _chain_id()
    if cid is not None:
        row["chain_id"] = cid
    append_row(plan_dir, row, EMIT_ACKNOWLEDGE)


def emit_index_regenerated(
    plan_dir: pathlib.Path,
    *,
    slug: str,
    open_count: int,
) -> None:
    """Append a `morning_review_index_regenerated` row.

    Per §H.impl.5 step 7. Fired after every triage gesture (idempotent).
    """
    row = {
        "session_id": _session_id(),
        "plan_slug": slug,
        "task_id": None,
        "transition": {"from": "deferred", "to": "deferred"},
        "mode_at_transition": {
            "overnight": _overnight_mode(),
            "guardrail": False,
        },
        "reason": f"morning_review_index_regenerated open_count={open_count}",
        "event_type": EVT_INDEX_REGENERATED,
        "open_count": open_count,
    }
    cid = _chain_id()
    if cid is not None:
        row["chain_id"] = cid
    append_row(plan_dir, row, EMIT_INDEX_REGEN)


def emit_archived(
    plan_dir: pathlib.Path,
    *,
    slug: str,
    daily_file: str,
    entry_count: int,
    sub_emitter: str = EMIT_GC,
) -> None:
    """Append a `morning_review_archived` row (§H.impl.7 step 5)."""
    row = {
        "session_id": _session_id(),
        "plan_slug": slug,
        "task_id": None,
        "transition": {"from": "deferred", "to": "deferred"},
        "mode_at_transition": {
            "overnight": _overnight_mode(),
            "guardrail": False,
        },
        "reason": (
            f"morning_review_archived daily_file={daily_file} "
            f"entry_count={entry_count}"
        ),
        "event_type": EVT_ARCHIVED,
        "daily_file": daily_file,
        "entry_count": entry_count,
    }
    cid = _chain_id()
    if cid is not None:
        row["chain_id"] = cid
    append_row(plan_dir, row, sub_emitter)


__all__ = [
    "append_row",
    "EMIT_BARE",
    "EMIT_LIST",
    "EMIT_SHOW",
    "EMIT_REACTIVATE",
    "EMIT_ROUTE_OUTSTANDING",
    "EMIT_ROUTE_MARKER",
    "EMIT_ABANDON",
    "EMIT_ACKNOWLEDGE",
    "EMIT_GC",
    "EMIT_INDEX_REGEN",
    "EMIT_MARK_FOR_EVAL",
    "EMIT_LABEL_SCORE",
    "EMIT_RETIRE_CASE",
    "EVT_TRIAGE_REACTIVATE",
    "EVT_TRIAGE_ROUTE_OUTSTANDING",
    "EVT_TRIAGE_ROUTE_MARKER",
    "EVT_TRIAGE_ABANDON",
    "EVT_ACKNOWLEDGED",
    "EVT_INDEX_REGENERATED",
    "EVT_ARCHIVED",
    "emit_triage",
    "emit_acknowledged",
    "emit_index_regenerated",
    "emit_archived",
]
