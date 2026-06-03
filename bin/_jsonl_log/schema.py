"""JSON Schema loader + cached validator for orchestrator_log_v1.

Per implplan §C.impl.3 — schema lives at `schemas/orchestrator_log_v1.schema.json`.
Per §C.impl.5 step 5 (schema validation post-flock, post-stamp).

Process-level cache: the validator is compiled once per process and
reused across `append_row` calls. Avoids repeated jsonschema-library
compilation overhead on tight chain loops.

Fallback discipline: if `jsonschema` is not installed (lightweight test
shims, minimal subprocess environments), we fall back to a small
hand-rolled validator that covers the closed enums + required fields
that the writer-time check actually cares about. The full Draft 2020-12
constraints (pattern, additionalProperties, oneOf) only run when
`jsonschema` is available. This keeps the writer functional in
constrained environments while preserving full validation under CI.
"""

from __future__ import annotations

import json
import pathlib
import threading
from typing import Any

_LOCK = threading.Lock()
_VALIDATOR: Any = None
_SCHEMA_DICT: dict | None = None

# Repo-root anchor: this file lives at bin/_jsonl_log/schema.py; schemas/ is
# two levels up.
_SCHEMA_PATH = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "schemas"
    / "orchestrator_log_v1.schema.json"
)


class SchemaValidationError(ValueError):
    """Raised when a row fails JSON Schema validation post-stamp."""


def _load_schema_dict() -> dict:
    global _SCHEMA_DICT
    if _SCHEMA_DICT is None:
        with _SCHEMA_PATH.open("r", encoding="utf-8") as fh:
            _SCHEMA_DICT = json.load(fh)
    return _SCHEMA_DICT


def _build_validator() -> Any:
    global _VALIDATOR
    if _VALIDATOR is not None:
        return _VALIDATOR
    try:
        import jsonschema  # type: ignore[import-not-found]
    except ImportError:
        # Fallback: a minimal validator. Returns None to signal the writer
        # should call `_minimal_validate` instead.
        _VALIDATOR = None
        return None
    schema = _load_schema_dict()
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    _VALIDATOR = cls(schema)
    return _VALIDATOR


def validate_row(row: dict) -> None:
    """Validate a fully-stamped row against orchestrator_log_v1.

    Raises `SchemaValidationError` with a human-readable message on
    mismatch. Compiles validator once per process; thread-safe.
    """
    with _LOCK:
        validator = _build_validator()
    if validator is None:
        _minimal_validate(row)
        return
    # jsonschema path
    errs = sorted(validator.iter_errors(row), key=lambda e: e.path)
    if errs:
        msgs = []
        for err in errs:
            loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
            msgs.append(f"at {loc}: {err.message}")
        raise SchemaValidationError("; ".join(msgs))


# --- Minimal fallback validator ---------------------------------------

_REQUIRED_STANDARD = (
    "schema_version",
    "ts",
    "session_id",
    "emitted_by",
    "plan_slug",
    "task_id",
    "transition",
    "mode_at_transition",
    "reason",
)

_VALID_SEVEN_STATUS = {
    "ready",
    "wip",
    "done",
    "deferred",
    "blocked",
    "cancelled",
    "unknown",
}


def _minimal_validate(row: dict) -> None:
    """Fallback validator covering only the closed-enum + required-field
    rules that are load-bearing at write time. Used when `jsonschema` is
    unavailable.
    """
    if not isinstance(row, dict):
        raise SchemaValidationError(f"row must be a dict; got {type(row).__name__}")
    for k in _REQUIRED_STANDARD:
        if k not in row:
            raise SchemaValidationError(f"missing required field: {k}")
    sv = row.get("schema_version")
    if not isinstance(sv, int) or sv < 1 or sv > 6:
        raise SchemaValidationError(
            f"schema_version must be int in [1,6]; got {sv!r}"
        )
    trans = row.get("transition")
    if not isinstance(trans, dict) or "from" not in trans or "to" not in trans:
        raise SchemaValidationError("transition must be {'from': ..., 'to': ...}")
    for key in ("from", "to"):
        val = trans[key]
        if val not in _VALID_SEVEN_STATUS:
            raise SchemaValidationError(
                f"transition.{key} must be in 7-status enum; got {val!r}"
            )
    mode = row.get("mode_at_transition")
    if not isinstance(mode, dict) or "overnight" not in mode or "guardrail" not in mode:
        raise SchemaValidationError(
            "mode_at_transition must be {'overnight': ..., 'guardrail': ...}"
        )
    # Recovery rows allow None on mode booleans; standard rows do not.
    is_recovery = row.get("session_id") == "_recovery"
    for k in ("overnight", "guardrail"):
        v = mode[k]
        if is_recovery:
            if not (isinstance(v, bool) or v is None):
                raise SchemaValidationError(
                    f"mode_at_transition.{k} must be bool or null on recovery row"
                )
        else:
            if not isinstance(v, bool):
                raise SchemaValidationError(
                    f"mode_at_transition.{k} must be bool; got {v!r}"
                )
