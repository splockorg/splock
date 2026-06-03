"""Structured stderr emission for refusal paths (implplan §E.impl).

Each refusal returns a structured JSON error to stderr + a stable exit
code from `exit_codes.py`. All refusal payloads use the `error` key as
the closed-enum discriminator.

Closed-enum error keys:

| Error key | Source | Exit code |
|---|---|---|
| `schema_rejected` | E.impl.3 R<n> parser; E.impl.4 schema validation | 4 |
| `unsupported_schema_version` | E.impl.4 forward-compat refusal | 5 |
| `task_outside_develop_plan_authority` | E.impl.3 — `deferred`/`abandoned` | 18 |
| `done_wip_rollback_refused` | E.impl.5 — missing OPERATOR_OVERRIDE_STATE | 19 |
| `iteration_overflow_refused` | E.impl.4 — 21st append after sentinel | 29 |
| `dual_retry_cap_mutex_violated` | E.impl.4 — co-populated retry+telemetry | 30 |
"""

from __future__ import annotations

import json
import sys
from typing import Any


def emit_refusal(payload: dict, stream=None) -> None:
    """Serialize a refusal payload as JSON to stderr (one line).

    `payload` MUST include `error` (closed-enum discriminator); other
    fields are payload-specific (e.g., `task_id`, `seen`, `supported`).
    """
    if stream is None:
        stream = sys.stderr
    line = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    stream.write(line + "\n")


def refusal_schema_rejected(reason: str, **fields: Any) -> dict:
    out = {"error": "schema_rejected", "reason": reason}
    out.update(fields)
    return out


def refusal_unsupported_schema_version(kind: str, seen: int, supported: list[int]) -> dict:
    return {
        "error": "unsupported_schema_version",
        "kind": kind,
        "seen": seen,
        "supported": supported,
    }


def refusal_task_outside_develop_plan_authority(task_id: str, status: str) -> dict:
    return {
        "error": "task_outside_develop_plan_authority",
        "task_id": task_id,
        "status": status,
    }


def refusal_done_wip_rollback(task_id: str) -> dict:
    return {
        "error": "done_wip_rollback_refused",
        "task_id": task_id,
        "reason": (
            "OPERATOR_OVERRIDE_STATE not set; "
            "develop-plan attempted done → wip rollback"
        ),
        "hint": "Re-invoke with OPERATOR_OVERRIDE_STATE=1, or triage manually.",
    }


def refusal_iteration_overflow(task_id: str) -> dict:
    return {
        "error": "iteration_overflow_refused",
        "task_id": task_id,
        "reason": "iteration_history storage cap exceeded; sentinel set; further appends refused.",
    }


def refusal_dual_retry_cap_mutex(task_id: str) -> dict:
    return {
        "error": "dual_retry_cap_mutex_violated",
        "task_id": task_id,
        "reason": (
            "retry_count and develop_plan_telemetry MUST NOT be co-populated on the "
            "same task in the same transaction (per §1.H dual retry-cap mutual exclusivity)."
        ),
    }
