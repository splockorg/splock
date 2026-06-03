"""Sole writer of `docs/plans/<slug>/_failures/<failure_id>.json`.

Per splock implplan §J.impl.5. Invoked from chain driver at
every `result: halted` transition with exit code ≥ 10. Failure-id is
deterministic — re-capture of the same halt is idempotent no-op
(returns exit code 39).
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re
from typing import Any, Optional

from bin._render_plan.atomic_write import write_atomic


_FAILURE_SCHEMA_PATH = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "schemas"
    / "failure_v1.schema.json"
)
_FAILURE_VALIDATOR: Any = None  # Lazy-built per-process; thread-safety per use site.


class FailureSchemaValidationError(ValueError):
    """Raised when a failure-capture row fails `schemas/failure_v1.schema.json`.

    Per acceptance suite Finding 2 (Option c hybrid): the highest-blast-radius
    §J writer validates against its schema at write time. Catches malformed
    emissions at the source rather than allowing them to land in
    `_failures/<id>.json` for downstream regression-replay to choke on.
    """


def _build_failure_validator() -> Any:
    """Lazy-build the jsonschema validator for failure_v1.

    Returns None when `jsonschema` is not installed; caller then skips
    runtime validation (preserves test-shim / minimal-environment behavior
    mirroring `bin/_jsonl_log/schema.py`).
    """
    global _FAILURE_VALIDATOR
    if _FAILURE_VALIDATOR is not None:
        return _FAILURE_VALIDATOR
    try:
        import jsonschema  # type: ignore[import-not-found]
    except ImportError:
        _FAILURE_VALIDATOR = False  # sentinel for "tried and unavailable"
        return None
    with _FAILURE_SCHEMA_PATH.open("r", encoding="utf-8") as fh:
        schema = json.load(fh)
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    _FAILURE_VALIDATOR = cls(schema)
    return _FAILURE_VALIDATOR


def _validate_failure_row(row: dict) -> None:
    """Validate a failure-capture row against schemas/failure_v1.schema.json.

    Raises `FailureSchemaValidationError` on mismatch with a structured
    message naming each invalid path. Pass-through no-op when `jsonschema`
    is unavailable.
    """
    validator = _build_failure_validator()
    if validator is None:
        return
    errs = sorted(validator.iter_errors(row), key=lambda e: list(e.absolute_path))
    if errs:
        msgs = [
            f"at {'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
            for err in errs
        ]
        raise FailureSchemaValidationError("; ".join(msgs))


HALT_REASONS: frozenset[str] = frozenset(
    {
        "retry_exceeded",
        "tampering_detected",
        "budget_exhausted",
        "wall_clock_cap",
        "guardrail_refused",
        "orphan_state",
        "verify_failed",
        "plan_rejected",
    }
)

PHASES: frozenset[str] = frozenset({"plan", "implplan", "code", "test"})


class InvalidHaltReasonError(ValueError):
    """Raised when `halt_reason` is not in the closed enum."""


class IdempotentCaptureNoop(RuntimeError):
    """Sentinel: same failure_id already on disk; capture is no-op.

    Caller (chain driver) maps to exit code 39.
    """


def _now_iso_z() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _failures_dir(plan_dir: pathlib.Path) -> pathlib.Path:
    return plan_dir / "_failures"


def derive_failure_id(chain_id: str, task_id: str, iteration: int) -> str:
    """Failure-id: `failure_<chain_id_short>_<task_id>_iter<N>`.

    `chain_id_short` is the last 8 hex chars of the chain_id digest if it
    is the full ISO chain pattern; otherwise the chain_id itself
    sanitized to alnum+_.
    """
    # Reduce chain_id to a short stable token by stripping non-alnum.
    short = re.sub(r"[^A-Za-z0-9]", "", chain_id)
    if len(short) > 16:
        short = short[-16:]
    return f"failure_{short}_{task_id}_iter{iteration}"


def capture(
    plan_dir: pathlib.Path,
    *,
    halt_reason: str,
    chain_id: str,
    session_id: str,
    plan_slug: str,
    halted_at_phase: str,
    halted_at_iteration: int,
    context_inputs: Optional[dict] = None,
    context_outputs: Optional[dict] = None,
    scorer_emissions: Optional[list[str]] = None,
    task_id: str = "T0",
) -> str:
    """Capture a halt as a `_failures/<failure_id>.json` file.

    Returns the `failure_id`. If a file with the same failure_id already
    exists, raises `IdempotentCaptureNoop` (caller maps to exit 39).
    """
    if halt_reason not in HALT_REASONS:
        raise InvalidHaltReasonError(
            f"halt_reason={halt_reason!r} not in {sorted(HALT_REASONS)}"
        )
    if halted_at_phase not in PHASES:
        raise InvalidHaltReasonError(
            f"halted_at_phase={halted_at_phase!r} not in {sorted(PHASES)}"
        )

    fdir = _failures_dir(plan_dir)
    fdir.mkdir(parents=True, exist_ok=True)

    failure_id = derive_failure_id(chain_id, task_id, halted_at_iteration)
    target = fdir / f"{failure_id}.json"
    if target.exists():
        raise IdempotentCaptureNoop(
            f"failure {failure_id} already captured at {target}"
        )

    row: dict[str, Any] = {
        "schema_version": 1,
        "failure_id": failure_id,
        "captured_at": _now_iso_z(),
        "chain_id": chain_id,
        "session_id": session_id,
        "plan_slug": plan_slug,
        "halt_reason": halt_reason,
        "halted_at_phase": halted_at_phase,
        "halted_at_iteration": halted_at_iteration,
        "context_inputs": context_inputs or {},
        "context_outputs": context_outputs or {},
        "scorer_emissions": list(scorer_emissions or []),
        "operator_disposition": None,
        "promoted_to_regression_case": None,
    }
    _validate_failure_row(row)  # Finding 2 hybrid: validate-at-write for §J's highest-blast-radius writer.
    body = json.dumps(row, indent=2, sort_keys=True, ensure_ascii=False)
    write_atomic(target, body + "\n")
    return failure_id


def read_failure(plan_dir: pathlib.Path, failure_id: str) -> Optional[dict]:
    target = _failures_dir(plan_dir) / f"{failure_id}.json"
    if not target.exists():
        return None
    return json.loads(target.read_text(encoding="utf-8"))


def mark_promoted(
    plan_dir: pathlib.Path,
    failure_id: str,
    case_id: str,
) -> None:
    """Update `_failures/<id>.json` setting promoted_to_regression_case."""
    target = _failures_dir(plan_dir) / f"{failure_id}.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["promoted_to_regression_case"] = case_id
    body = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    write_atomic(target, body + "\n")


__all__ = [
    "HALT_REASONS",
    "PHASES",
    "InvalidHaltReasonError",
    "IdempotentCaptureNoop",
    "derive_failure_id",
    "capture",
    "read_failure",
    "mark_promoted",
]
