"""`develop_plan_telemetry` sidecar reader/writer (implplan §E.impl.4).

The sidecar is OPTIONAL on a task-entry inside `_state.json`. The sole
writer is `bin/update_orchestrator --from-develop-plan`. Schema lives at
`schemas/develop_plan_telemetry_v1.schema.json` (Draft 2020-12).

Storage cap (per plan §E.4 + ratified §E.impl.10 #2):

- ≤19 entries: append normally; `iteration_overflow: false`.
- 20th-position append: append normally (length goes 19 → 20).
- 21st-position append: REPLACE with `overflow_sentinel` row;
  `iteration_overflow: true`.
- 22nd+ append: REFUSE (exit 29).

Closed-enum strictness for `outcome` and `source` enforced via
`jsonschema` against the compiled schema (no LLM in loop).

Forward-compat refusal (per §B.impl.6 pattern):

| Seen | Behavior |
|---|---|
| `schema_version: 1` | Accept |
| `schema_version: 2+` | Refuse (exit 5, `unsupported_schema_version`) |
| `schema_version: 0` | Refuse (exit 5, `schema_version_too_old` variant) |
| `schema_version` missing | Refuse (exit 4, `schema_rejected`) |
"""

from __future__ import annotations

import dataclasses
import datetime
import json
from pathlib import Path
from typing import Optional

import jsonschema

SUPPORTED_VERSIONS_TELEMETRY: tuple[int, ...] = (1,)
CURRENT_SCHEMA_VERSION_TELEMETRY: int = 1
SOURCE_BRIDGE_V1: str = "develop-plan-v1-bridge"

# Closed enum for `outcome` (matches schema enum)
OUTCOME_NEEDS_REVISION = "needs_revision"
OUTCOME_APPROVED = "approved"
OUTCOME_BLOCKED = "blocked"
OUTCOME_OVERFLOW_SENTINEL = "overflow_sentinel"
OUTCOMES = frozenset(
    {
        OUTCOME_NEEDS_REVISION,
        OUTCOME_APPROVED,
        OUTCOME_BLOCKED,
        OUTCOME_OVERFLOW_SENTINEL,
    }
)


# Lazy-loaded schema (avoids reading the file at module import time).
_SCHEMA_CACHE: Optional[dict] = None


class TelemetrySchemaError(ValueError):
    """Raised when `develop_plan_telemetry` content fails schema validation
    (exit code 4 — schema_rejected)."""


class TelemetryUnsupportedVersionError(ValueError):
    """Raised when `schema_version` is not in SUPPORTED_VERSIONS_TELEMETRY
    (exit code 5 — unsupported_schema_version)."""


class IterationOverflowError(RuntimeError):
    """Raised when an append is attempted after the overflow sentinel
    is already present (exit code 29)."""


class DualRetryCapMutexViolation(RuntimeError):
    """Raised when a task entry has both `retry_count` and
    `develop_plan_telemetry` populated (exit code 30)."""


def _load_schema() -> dict:
    """Load + cache the v1 schema."""
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        # Resolve via this module's repo-root: bin/_update_orchestrator/<this>.py → REPO_ROOT
        repo_root = Path(__file__).resolve().parents[2]
        schema_path = repo_root / "schemas" / "develop_plan_telemetry_v1.schema.json"
        _SCHEMA_CACHE = json.loads(schema_path.read_text(encoding="utf-8"))
    return _SCHEMA_CACHE


def _now_iso_z() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def validate_schema_version(telemetry: dict) -> None:
    """Pre-validation of `schema_version` field per §B.impl.6 pattern.

    Raises:
        TelemetrySchemaError: missing field (exit 4).
        TelemetryUnsupportedVersionError: unsupported version (exit 5).
    """
    if not isinstance(telemetry, dict):
        raise TelemetrySchemaError("develop_plan_telemetry must be an object")
    if "schema_version" not in telemetry:
        raise TelemetrySchemaError("develop_plan_telemetry.schema_version is required")
    seen = telemetry["schema_version"]
    if not isinstance(seen, int) or seen < 1:
        raise TelemetryUnsupportedVersionError(
            f"schema_version={seen!r} is too old or invalid (supported: {list(SUPPORTED_VERSIONS_TELEMETRY)})"
        )
    if seen not in SUPPORTED_VERSIONS_TELEMETRY:
        raise TelemetryUnsupportedVersionError(
            f"schema_version={seen} not supported; supported: {list(SUPPORTED_VERSIONS_TELEMETRY)}"
        )


def validate_telemetry(telemetry: dict) -> None:
    """Full-shape validation of a `develop_plan_telemetry` block.

    Raises:
        TelemetrySchemaError: shape violation (exit 4).
        TelemetryUnsupportedVersionError: bad `schema_version` (exit 5).
    """
    validate_schema_version(telemetry)
    schema = _load_schema()
    try:
        # Explicit Draft 2020-12 per §E.impl.4 spec; per F-03 of §E.impl
        # mid-section Sonnet review 2026-05-21 (don't rely on jsonschema's
        # $schema-URI auto-dispatch — pin the validator class).
        jsonschema.validate(
            instance=telemetry,
            schema=schema,
            cls=jsonschema.Draft202012Validator,
        )
    except jsonschema.ValidationError as exc:
        raise TelemetrySchemaError(str(exc)) from exc


def empty_telemetry() -> dict:
    """Return a freshly-initialized sidecar with empty history."""
    return {
        "source": SOURCE_BRIDGE_V1,
        "schema_version": CURRENT_SCHEMA_VERSION_TELEMETRY,
        "iteration_history": [],
        "iteration_overflow": False,
    }


def check_dual_retry_cap_mutex(task_entry: dict) -> None:
    """Raise if both `retry_count` and `develop_plan_telemetry` are
    populated on the same task entry (per E.7 success criterion 3).

    A `retry_count: null` or `retry_count: 0` IS counted as populated
    only if it was explicitly set; the v1.3 spec wording is that both
    fields appearing on the same task is the violation. We interpret
    "populated" as "key present AND value not None".
    """
    has_retry = ("retry_count" in task_entry) and (task_entry["retry_count"] is not None)
    has_telemetry = (
        "develop_plan_telemetry" in task_entry
        and task_entry["develop_plan_telemetry"] is not None
    )
    if has_retry and has_telemetry:
        raise DualRetryCapMutexViolation(
            "retry_count and develop_plan_telemetry MUST NOT be co-populated "
            "on the same task entry (per §1.H mutual exclusivity)."
        )


def append_iteration_row(
    task_entry: dict,
    round_n: int,
    *,
    started_at: Optional[str] = None,
    ended_at: Optional[str] = None,
    outcome: str = OUTCOME_NEEDS_REVISION,
    evaluator_notes_ref: Optional[str] = None,
) -> dict:
    """Append a row to `task_entry.develop_plan_telemetry.iteration_history`.

    Storage-cap discipline (per §E.impl.4):
    - len <= 19: append normally.
    - len == 20: this is the 21st position; REPLACE the new row with
      an `overflow_sentinel` entry and set `iteration_overflow: true`.
    - len >= 21: REFUSE with `IterationOverflowError` (exit 29).

    Mutates `task_entry` in place and also returns it.

    Raises:
        IterationOverflowError: cap exhausted (exit 29).
        DualRetryCapMutexViolation: retry_count co-populated (exit 30).
        TelemetrySchemaError: post-append schema validation failure.
    """
    # Pre-write mutex check: if retry_count is populated, even creating a
    # telemetry namespace now would violate the §1.H mutual-exclusivity rule.
    has_retry = ("retry_count" in task_entry) and (task_entry["retry_count"] is not None)
    if has_retry:
        raise DualRetryCapMutexViolation(
            "retry_count populated on task entry; refusing to create "
            "develop_plan_telemetry sidecar (per §1.H mutual exclusivity)."
        )
    check_dual_retry_cap_mutex(task_entry)

    telemetry = task_entry.get("develop_plan_telemetry")
    if telemetry is None:
        telemetry = empty_telemetry()
        task_entry["develop_plan_telemetry"] = telemetry

    # Validate version BEFORE mutation
    validate_schema_version(telemetry)

    history: list = telemetry.setdefault("iteration_history", [])
    current_len = len(history)

    if current_len >= 21:
        raise IterationOverflowError(
            f"iteration_history at sentinel cap ({current_len} entries); further appends refused."
        )

    ts = _now_iso_z()
    started_at = started_at or ts
    ended_at = ended_at or ts

    if current_len == 20:
        # 21st position — replace with sentinel
        sentinel_row = {
            "round": round_n,
            "started_at": started_at,
            "ended_at": ended_at,
            "outcome": OUTCOME_OVERFLOW_SENTINEL,
            "evaluator_notes_ref": "<storage-cap-hit; further appends refused>",
        }
        history.append(sentinel_row)
        telemetry["iteration_overflow"] = True
    else:
        row = {
            "round": round_n,
            "started_at": started_at,
            "ended_at": ended_at,
            "outcome": outcome,
        }
        if evaluator_notes_ref is not None:
            row["evaluator_notes_ref"] = evaluator_notes_ref
        history.append(row)

    # Final shape validation
    validate_telemetry(telemetry)
    return task_entry
