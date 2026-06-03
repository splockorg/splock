"""Thin delegate to `bin/_jsonl_log/writer.append_row` (implplan §E.impl + §C.impl.6).

This module exists for two reasons:

1. Tests mock `bin._update_orchestrator.log_emit.append_row` to decouple
   from the real `_orchestrator_log.jsonl` writer (same pattern as
   `bin._marker.log_emit`).
2. Sub-emitter constants are defined here so `main.py` doesn't need to
   import from `bin._jsonl_log` directly.

`emitted_by` value MUST match a KNOWN_WRITERS entry per C.impl.6 layer 1:

| Mode | `emitted_by` value |
|---|---|
| Base CLI (`bin/update_orchestrator <slug> <task_id> <status>`) | `bin/update_orchestrator` |
| `--from-develop-plan` subcommand | `bin/update_orchestrator --from-develop-plan` |

Both entries already exist in KNOWN_WRITERS (writers.py lines 36-37).

Extra payload fields (e.g., `event_type` discriminator, `override_in_effect`)
flow through the row dict; they are payload-level forensic markers and
are NOT enforced by the §C row schema's closed-enum on `transition.to`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# Top-level import; tests mock via `mock.patch("bin._update_orchestrator.log_emit.append_row")`.
try:
    from bin._jsonl_log.writer import append_row  # noqa: F401
except ImportError:  # pragma: no cover — only hit during isolated unit-test boot
    def append_row(plan_dir, row, emitted_by):  # type: ignore[no-redef]
        raise RuntimeError(
            "bin/_jsonl_log/writer.py not yet shipped (§C dependency). "
            "Tests should mock `bin._update_orchestrator.log_emit.append_row`."
        )


# Sub-emitter constants (one per CLI invocation mode).
EMIT_BASE = "bin/update_orchestrator"
EMIT_FROM_DEVELOP_PLAN = "bin/update_orchestrator --from-develop-plan"


# event_type discriminators for orch_status_render T4 — emitted alongside
# the actual state-transition row when the wired render call succeeds or
# fails. These are payload-level free-form strings; the schema does NOT
# enforce a closed enum on `event_type` (see schemas/orchestrator_log_v1
# .schema.json StandardRow.properties.event_type description).
EVENT_TYPE_STATE_MD_RENDERED = "state_md_rendered"
EVENT_TYPE_STATE_MD_RENDER_FAILED = "state_md_render_failed"


def session_id() -> str:
    """Read `$CLAUDE_SESSION_ID`, else stub a placeholder."""
    return os.environ.get("CLAUDE_SESSION_ID") or "sess_00000000"


def emit_transition(
    plan_dir: Path,
    plan_slug: str,
    task_id: Optional[str],
    transition_from: str,
    transition_to: str,
    reason: str,
    *,
    emitted_by: str,
    chain_id: Optional[str] = None,
    override_in_effect: Optional[dict] = None,
    overnight: bool = False,
    guardrail: bool = False,
    retry_count: Optional[int] = None,
    pointer: Optional[str] = None,
    extra: Optional[dict] = None,
) -> None:
    """Emit one transition row via the §C shared writer.

    Caller is responsible for ensuring `emitted_by` is a registered
    KNOWN_WRITERS value (validated PRE-FLOCK in `append_row`).
    """
    row: dict = {
        "session_id": session_id(),
        "plan_slug": plan_slug,
        "task_id": task_id,
        "transition": {"from": transition_from, "to": transition_to},
        "mode_at_transition": {"overnight": overnight, "guardrail": guardrail},
        "reason": reason,
    }
    if chain_id is not None:
        row["chain_id"] = chain_id
    if override_in_effect is not None:
        row["override_in_effect"] = override_in_effect
    if retry_count is not None:
        row["retry_count"] = retry_count
    if pointer is not None:
        row["pointer"] = pointer
    if extra:
        # `event_type` lives here as a payload-level discriminator (not in
        # the §C row schema's enum surface; tolerated as additive field).
        # The §C schema does NOT include `event_type`, so this only flows
        # through when callers also pass it via extra and the schema's
        # additionalProperties policy permits it. The current §C schema
        # has explicit `properties` listings and no `additionalProperties:
        # false`, so additive payload fields are accepted.
        row.update(extra)
    append_row(plan_dir, row, emitted_by)


def emit_state_md_rendered(
    plan_dir: Path,
    plan_slug: str,
    *,
    emitted_by: str,
    chain_id: Optional[str] = None,
    overnight: bool = False,
    guardrail: bool = False,
) -> None:
    """Emit an observability row stamping a successful `_orchestrator.md` render.

    Per orch_status_render T4 — fires from `_dispatch_base` and
    `_dispatch_from_develop_plan` after the wired
    `render_state_under_flock` call succeeds. The row carries
    `event_type: "state_md_rendered"` as a payload-level discriminator.

    `transition.from`/`transition.to` are both `unknown` because this is
    an observability row, not an actual state transition (the actual
    transition row is emitted separately). The §C schema's StandardRow
    SevenStatus enum permits `unknown` on both ends, so the schema
    validates cleanly.
    """
    row: dict = {
        "session_id": session_id(),
        "plan_slug": plan_slug,
        "task_id": None,
        "transition": {"from": "unknown", "to": "unknown"},
        "mode_at_transition": {"overnight": overnight, "guardrail": guardrail},
        "reason": "state_md rendered successfully",
        "event_type": EVENT_TYPE_STATE_MD_RENDERED,
    }
    if chain_id is not None:
        row["chain_id"] = chain_id
    append_row(plan_dir, row, emitted_by)


def emit_state_md_render_failed(
    plan_dir: Path,
    plan_slug: str,
    *,
    emitted_by: str,
    error: str,
    chain_id: Optional[str] = None,
    overnight: bool = False,
    guardrail: bool = False,
) -> None:
    """Emit an observability row stamping a failed `_orchestrator.md` render.

    Per orch_status_render T4 — fires from `_dispatch_base` and
    `_dispatch_from_develop_plan` when the wired
    `render_state_under_flock` call raises. The row carries
    `event_type: "state_md_render_failed"` plus a `reason` string that
    incorporates the underlying exception's message.

    The actual state-transition row is ALWAYS still emitted by the
    caller — render failure does not lose the audit row for the state
    mutation that did succeed.
    """
    row: dict = {
        "session_id": session_id(),
        "plan_slug": plan_slug,
        "task_id": None,
        "transition": {"from": "unknown", "to": "unknown"},
        "mode_at_transition": {"overnight": overnight, "guardrail": guardrail},
        "reason": f"state_md render failed: {error}",
        "event_type": EVENT_TYPE_STATE_MD_RENDER_FAILED,
    }
    if chain_id is not None:
        row["chain_id"] = chain_id
    append_row(plan_dir, row, emitted_by)
