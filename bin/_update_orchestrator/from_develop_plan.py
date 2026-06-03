"""6 → 7 status mapping LUT + iteration_history side-effect dispatch
(implplan §E.impl.3 + §E.impl.4).

This module codifies plan §E.3 lines 1444-1473 as a module-level Python
LUT. The LUT is the SOLE source of truth; no mapping logic lives
elsewhere. No LLM in the loop (per plan §E.3 line 1446-1447: "pure
code... no judgment, no heuristic, no LLM").

R<n> parsing rules (per implplan §E.impl.3 + plan §E.3 lines 1468-1469):

| Input | Behavior |
|---|---|
| `revisions_requested:R1` | Accept; n=1 |
| `revisions_requested:R5` | Accept; n=5 |
| `revisions_requested:R0` | Refuse (exit 4) |
| `revisions_requested:R-1` | Refuse (exit 4) |
| `revisions_requested:Rfoo` | Refuse (exit 4) |
| `revisions_requested:R101` | Refuse (exit 4) |
| `revisions_requested` (no `:R<n>`) | Refuse (exit 4) |

Reverse mapping is NOT supported (per plan §E.3 line 1458-1459).

`deferred` / `cancelled` outside scope (per plan §E.3 line 1470-1472):
if `_state.json` shows a task in `deferred` or `cancelled`,
`--from-develop-plan` refuses with exit code 18. Note: plan §E.3
prose says "deferred / abandoned" but the canonical 7-status enum
shipped in §A.impl uses `cancelled` (not `abandoned`); we honor the
shipped enum.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

SIDE_EFFECT_APPEND_ITERATION = "append_iteration_history"


# Native develop-plan status enum (closed set).
NATIVE_STATUS_NOT_STARTED = "not_started"
NATIVE_STATUS_IN_PROGRESS = "in_progress"
NATIVE_STATUS_AWAITING_EVAL = "awaiting_eval"
NATIVE_STATUS_REVISIONS_REQUESTED = "revisions_requested"
NATIVE_STATUS_COMPLETED = "completed"
NATIVE_STATUS_BLOCKED = "blocked"

# Statuses that --from-develop-plan refuses to touch (E.impl.3 boundary).
# `abandoned` is NOT included — it is not a member of the canonical
# 7-status enum (which uses `cancelled` instead). Including it here
# would create a phantom branch that could only ever fire on a
# corrupted state file, and would crash the forensic-row emit path
# under the §C writer's pre-flock validator. Per F-01 of §E.impl
# mid-section Sonnet review 2026-05-21.
TASK_OUTSIDE_DEVELOP_PLAN_AUTHORITY = frozenset({"deferred", "cancelled"})


@dataclass(frozen=True)
class MappingResult:
    canonical: str
    side_effect: Optional[str]
    round_n: Optional[int]  # only populated for revisions_requested:R<n>


DEVELOP_PLAN_STATUS_LUT: dict[str, dict] = {
    NATIVE_STATUS_NOT_STARTED: {"canonical": "ready", "side_effect": None},
    NATIVE_STATUS_IN_PROGRESS: {"canonical": "wip", "side_effect": None},
    # `awaiting_eval` maps to `unknown` because the 7-status enum has no
    # `needs-review` member; the develop-plan native concept is held in
    # the sidecar's `iteration_history` rather than promoted to a
    # canonical status. Per plan §E.3 line 1483: "awaiting_eval → wip
    # OR unknown (developer-plan's evaluator gate; not Ralph)" — we use
    # `unknown` to preserve the distinction from `wip` ("actively
    # iterating") while staying within the canonical 7-status enum.
    NATIVE_STATUS_AWAITING_EVAL: {"canonical": "unknown", "side_effect": None},
    NATIVE_STATUS_REVISIONS_REQUESTED: {
        "canonical": "wip",
        "side_effect": SIDE_EFFECT_APPEND_ITERATION,
    },
    NATIVE_STATUS_COMPLETED: {"canonical": "done", "side_effect": None},
    NATIVE_STATUS_BLOCKED: {"canonical": "blocked", "side_effect": None},
}


_REVISIONS_PATTERN = re.compile(r"^revisions_requested:R(-?\d+)$")
_REVISIONS_PROSE_PATTERN = re.compile(r"^revisions_requested(:.*)?$")


class NativeStatusParseError(ValueError):
    """Raised when a native status string is malformed (exit code 4)."""


class TaskOutsideAuthorityError(ValueError):
    """Raised when `--from-develop-plan` targets a task currently in
    `deferred` / `abandoned` / `cancelled` (exit code 18)."""


def parse_revisions_requested(native: str) -> int:
    """Parse `revisions_requested:R<n>` and return n.

    Refuses with `NativeStatusParseError` (exit 4) for:
    - n <= 0
    - n > 100
    - non-numeric
    - missing `:R<n>` suffix
    """
    m = _REVISIONS_PATTERN.match(native)
    if not m:
        # Distinguish "missing suffix" from "malformed suffix"
        if native == NATIVE_STATUS_REVISIONS_REQUESTED:
            raise NativeStatusParseError(
                f"missing round counter: expected '{NATIVE_STATUS_REVISIONS_REQUESTED}:R<n>'"
            )
        if _REVISIONS_PROSE_PATTERN.match(native):
            raise NativeStatusParseError(
                f"malformed round counter in {native!r}: expected ':R<n>' "
                "with n a positive integer ≤ 100"
            )
        raise NativeStatusParseError(
            f"native status {native!r} is not a recognized revisions_requested form"
        )
    n = int(m.group(1))
    if n <= 0:
        raise NativeStatusParseError(
            f"round counter must be positive integer; got n={n}"
        )
    if n > 100:
        raise NativeStatusParseError(
            f"round counter exceeds sanity cap (100); got n={n}"
        )
    return n


def map_develop_plan_to_canonical(native: str) -> MappingResult:
    """Map a develop-plan native status to canonical 7-status form.

    Returns a `MappingResult` carrying:
    - `canonical`: one of the 7-status enum values
    - `side_effect`: SIDE_EFFECT_APPEND_ITERATION or None
    - `round_n`: parsed `R<n>` value (only for revisions_requested)

    Raises:
        NativeStatusParseError: unknown native or malformed R<n> (exit 4).
    """
    # revisions_requested has a special `:R<n>` form
    if native.startswith(NATIVE_STATUS_REVISIONS_REQUESTED):
        round_n = parse_revisions_requested(native)
        entry = DEVELOP_PLAN_STATUS_LUT[NATIVE_STATUS_REVISIONS_REQUESTED]
        return MappingResult(
            canonical=entry["canonical"],
            side_effect=entry["side_effect"],
            round_n=round_n,
        )
    if native not in DEVELOP_PLAN_STATUS_LUT:
        raise NativeStatusParseError(
            f"unknown develop-plan native status: {native!r}; "
            f"valid: {sorted(DEVELOP_PLAN_STATUS_LUT.keys())}"
        )
    entry = DEVELOP_PLAN_STATUS_LUT[native]
    return MappingResult(
        canonical=entry["canonical"],
        side_effect=entry["side_effect"],
        round_n=None,
    )


def ensure_task_in_authority(current_status: Optional[str], task_id: str) -> None:
    """Raise `TaskOutsideAuthorityError` (exit 18) if the current canonical
    status is one develop-plan has no authority to touch.

    The "outside-authority" set is `deferred` / `abandoned` / `cancelled`
    per plan §E.3 line 1470-1472.
    """
    if current_status in TASK_OUTSIDE_DEVELOP_PLAN_AUTHORITY:
        raise TaskOutsideAuthorityError(
            f"task {task_id!r} is in canonical status {current_status!r}; "
            "develop-plan has no authority to mutate."
        )
