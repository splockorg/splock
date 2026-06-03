"""`append_row` — the SOLE entry point for writes to `_orchestrator_log.jsonl`.

Per implplan §C.impl.5 (atomic append discipline + flock + fsync) and
§C.impl.6 (closed-enum enforcement, Finding 28). Every registered emitter
in `KNOWN_WRITERS` (writers.py) calls `append_row` exactly once per
transition; no CLI opens the JSONL directly.

Compound-write caller contract (per implplan §C.impl.5 lines 1719-1730,
F-01 from the pre-Phase 1 Sonnet review)
-----------------------------------------------------------------------
The `append_row` writer locks only `_orchestrator_log.jsonl.lock`. Callers
performing a COMPOUND write — e.g., `bin/update_orchestrator` writing
`_state.json` AND appending to the JSONL — MUST acquire locks in a
deterministic order to avoid deadlock and divergence:

    1. Acquire `_state.json.lock` FIRST.
    2. THEN call `append_row(...)` which acquires
       `_orchestrator_log.jsonl.lock` internally.
    3. Inside the inner critical section, perform the `_state.json` write
       BEFORE calling `append_row`, so the JSONL row only persists after
       the state mutation is durable.

Lock nesting order is FIXED — never reverse it. This contract is owned by
§E.impl (where `bin/update_orchestrator` lives); documented here so the
§C writer's lock scope is unambiguous.

Operation sequence (strict order, per §C.impl.5 lines 1684-1703)
-----------------------------------------------------------------
1. Closed-enum rejection (PRE-FLOCK, cheap):
   - `emitted_by in KNOWN_WRITERS` → else `UnregisteredWriterError`
   - `transition.from` / `transition.to` in `SEVEN_STATUS` → else
     `InvalidTransitionError`
2. Stamp writer-supplied fields: `ts`, `writer_pid`, `writer_host`,
   `schema_version`, `emitted_by`.
3. Acquire flock on `<plan_dir>/_orchestrator_log.jsonl.lock` (blocking).
4. Recovery check: `_validate_or_truncate_last_line(jsonl_path)`.
5. Schema validation post-stamp.
6. Write: open in `ab`; write `json.dumps(row) + "\n"`; `flush()`;
   `os.fsync(fileno)`.
7. Release flock implicit on `with`-block exit.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import socket
from typing import Any

from .flock_helpers import acquire_exclusive, jsonl_path
from .schema import SchemaValidationError, validate_row
from .writers import KNOWN_WRITERS, SEVEN_STATUS, SUPPORTED_VERSIONS_LOG


# The schema-version that newly-emitted rows are stamped with at write
# time. Older rows in the same JSONL may carry any version in
# SUPPORTED_VERSIONS_LOG and remain readable; only new emissions stamp
# the latest.
CURRENT_SCHEMA_VERSION: int = max(SUPPORTED_VERSIONS_LOG)


class UnregisteredWriterError(ValueError):
    """Raised when `emitted_by` is not in KNOWN_WRITERS (per §C.impl.5 step 1)."""


class InvalidTransitionError(ValueError):
    """Raised when `transition.from` / `.to` is not in the 7-status enum."""


def _now_iso_z() -> str:
    """Return ISO-8601 UTC with trailing `Z`, second resolution.

    Format matches `IsoTimestampZ` pattern in the row schema.
    """
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _validate_pre_flock(row: dict, emitted_by: str) -> None:
    """Cheap pre-flock validation per implplan §C.impl.5 step 1.

    Done BEFORE flock acquisition to minimize lock contention on
    bad-input cases.
    """
    if emitted_by not in KNOWN_WRITERS:
        raise UnregisteredWriterError(
            f"emitted_by={emitted_by!r} is not in KNOWN_WRITERS; "
            f"register the writer in bin/_jsonl_log/writers.py and bump "
            f"SUPPORTED_VERSIONS_LOG per implplan §C.impl.3"
        )
    trans = row.get("transition")
    if not isinstance(trans, dict):
        raise InvalidTransitionError(
            f"row['transition'] must be a dict with 'from' and 'to'; got {type(trans).__name__}"
        )
    for key in ("from", "to"):
        if key not in trans:
            raise InvalidTransitionError(
                f"row['transition'] missing required key {key!r}"
            )
        val = trans[key]
        if val not in SEVEN_STATUS:
            raise InvalidTransitionError(
                f"transition.{key}={val!r} is not in 7-status enum; "
                f"valid values: {sorted(SEVEN_STATUS)}"
            )


def _stamp_writer_fields(row: dict, emitted_by: str) -> dict:
    """Step 2 of §C.impl.5: stamp `ts`, `writer_pid`, `writer_host`,
    `schema_version`, `emitted_by`.

    Returns a new dict (does not mutate caller's row).
    """
    stamped = dict(row)
    stamped["ts"] = _now_iso_z()
    stamped["writer_pid"] = os.getpid()
    stamped["writer_host"] = socket.gethostname()
    stamped["schema_version"] = CURRENT_SCHEMA_VERSION
    stamped["emitted_by"] = emitted_by
    return stamped


def _append_one_row_unlocked(jsonl: pathlib.Path, row: dict) -> None:
    """Atomic append + fsync. PRESUMES flock is already held.

    This helper is invoked both by `append_row` (the public entry point)
    and by `recovery._validate_or_truncate_last_line` (which calls this
    directly to avoid recursive flock acquisition on its `_corrupt_truncated`
    audit row).

    Single `write()` call ensures the bytes hit the kernel in one
    syscall; `flush()` + `os.fsync(fileno())` ensure they hit disk before
    the function returns.
    """
    # Canonical form: sorted keys + compact separators. Matches the merge
    # driver's canonical serialization (bin/_git_merge_jsonl/merge.py) so
    # that `merge(A, A, A) == A` is byte-identical on fresh logs — the
    # implplan §C.impl.9 idempotency property.
    payload = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    with jsonl.open("ab") as fh:
        fh.write(payload.encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())


def append_row(
    plan_dir: pathlib.Path,
    row: dict,
    emitted_by: str,
) -> None:
    """Append a transition-log row to `<plan_dir>/_orchestrator_log.jsonl`.

    This is the SOLE entry point for writes per implplan §C.impl.5
    line 1666. Every registered emitter MUST go through this function.

    Parameters
    ----------
    plan_dir : pathlib.Path
        The slug directory `docs/plans/<slug>/`. Auto-created if missing.
    row : dict
        All caller-supplied fields per §C.impl.3. Writer-stamped fields
        (`ts`, `writer_pid`, `writer_host`, `schema_version`,
        `emitted_by`) MUST be omitted by the caller — they are stamped
        by this function.
    emitted_by : str
        The writer's `EMITTED_BY` constant. Must be in `KNOWN_WRITERS`
        or an `UnregisteredWriterError` is raised PRE-FLOCK.

    Raises
    ------
    UnregisteredWriterError
        If `emitted_by not in KNOWN_WRITERS`. Raised BEFORE flock so
        lock contention is not affected by bad input.
    InvalidTransitionError
        If `transition.from` / `.to` is missing or not in the 7-status
        enum. Raised BEFORE flock.
    SchemaValidationError
        If the fully-stamped row fails JSON Schema validation. Raised
        AFTER flock + recovery check; the file state up to this point
        (including any recovery-audit row) is preserved.
    """
    # Step 1 — pre-flock validation.
    _validate_pre_flock(row, emitted_by)

    # Step 2 — stamp writer fields.
    stamped = _stamp_writer_fields(row, emitted_by)

    # Step 3 — acquire flock.
    target = jsonl_path(plan_dir)
    with acquire_exclusive(plan_dir):
        # Step 4 — recovery check.
        from .recovery import _validate_or_truncate_last_line

        _validate_or_truncate_last_line(target)

        # Step 5 — schema validation post-stamp.
        validate_row(stamped)

        # Step 6 — write + flush + fsync (lock still held).
        _append_one_row_unlocked(target, stamped)

        # Step 7 — flock released by context manager on exit.


__all__ = [
    "append_row",
    "UnregisteredWriterError",
    "InvalidTransitionError",
    "CURRENT_SCHEMA_VERSION",
]
# Note: `_append_one_row_unlocked` is a cross-module private contract used
# only by bin/_jsonl_log/recovery.py to write the `_corrupt_truncated`
# audit row while holding the caller's flock. Intentionally NOT exported
# from __all__ — the leading underscore is the canonical privacy signal.
