"""Thin delegate to `bin/_jsonl_log/writer.append_row`.

Per implplan §K.impl.4 / §K.impl.5 / triage F-03: this module exists to
emit marker-lifecycle events (marker_created / marker_closed /
marker_create_refused / prefix_registered / marker_validate_emit) to the
canonical `_orchestrator_log.jsonl` substrate.

Critical separation (triage F-03):

- The `emitted_by` value passed to `append_row` is ALWAYS a §C
  KNOWN_WRITERS sub-emitter value (e.g., `bin/marker:create`), reflecting
  the CLI subcommand that ran. It is DISTINCT from the marker-entry
  `emitted_by` (which is `bin/marker` / `bin/morning-review:route-marker` /
  `bin/route_issue:route-marker` / `agent` per §K.impl.3).
- The marker-entry `emitted_by` is for `list.md` storage only; it lives
  in the `reason`/`payload` field of the log row, not the writer-level
  attribution.

§C import-late discipline (triage `§C dependency`): we import
`bin._jsonl_log.writer.append_row` at top level. §C's module is built in
parallel; once integration tests run the real import is available.
Tests mock `bin._marker.log_emit.append_row` (the symbol re-exported here)
to decouple from §C build timing.
"""

from __future__ import annotations

import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Top-level import: real symbol after §C ships; tests mock this symbol.
try:
    from bin._jsonl_log.writer import append_row  # noqa: F401
except ImportError:  # pragma: no cover — only hit during isolated unit-test boot
    # The fallback only triggers if §C writer is not yet shipped AND the test
    # harness has not installed a mock. In that case, raise to surface the
    # gap rather than silently swallowing log emission.
    def append_row(plan_dir, row, emitted_by):  # type: ignore[no-redef]
        raise RuntimeError(
            "bin/_jsonl_log/writer.py not yet shipped (§C dependency). "
            "Tests should mock `bin._marker.log_emit.append_row`."
        )


# Sub-emitter constants (one per CLI subcommand).
EMIT_CREATE = "bin/marker:create"
EMIT_CLOSE = "bin/marker:close"
EMIT_VALIDATE = "bin/marker:validate"
EMIT_REGISTER_PREFIX = "bin/marker:register-prefix"
EMIT_ROUTE_MARKER = "bin/marker:route-marker"
EMIT_BARE = "bin/marker"  # for non-subcommand invocations (e.g., list / show emit nothing)


def _now_iso() -> str:
    """UTC timestamp with Z suffix per §C.impl.3 `ts` field."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit_marker_created(
    plan_dir: Optional[Path],
    marker_id: str,
    title: str,
    target: str,
    source_plan: Optional[str],
    detail_file: str,
    marker_emitted_by: str,
    sub_emitter: str = EMIT_CREATE,
) -> None:
    """Emit a `marker_created` row.

    `plan_dir` is the slug-dir for the originating plan, or None for
    cross-cutting (source_plan=null) markers. When None, the marker
    substrate's own dir `docs/plans/scheduled_markers/` is used so the
    JSONL has a home; this is a documented choice per §K.impl.8.
    """
    target_dir = _resolve_plan_dir(plan_dir, source_plan)
    row = {
        "transition": {"from": "ready", "to": "deferred"},
        "reason": (
            f"marker_created {marker_id}: {title} "
            f"[marker_emitted_by={marker_emitted_by}; "
            f"detail_file={detail_file}; target={target}]"
        ),
        "task_id": None,
        "session_id": _session_id(),
        "plan_slug": source_plan if source_plan else "scheduled_markers",
        "mode_at_transition": {"overnight": False, "guardrail": True},
    }
    _append(target_dir, row, sub_emitter)


def emit_marker_closed(
    plan_dir: Optional[Path],
    marker_id: str,
    resolution: str,
    source_plan: Optional[str],
    sub_emitter: str = EMIT_CLOSE,
) -> None:
    target_dir = _resolve_plan_dir(plan_dir, source_plan)
    row = {
        "transition": {"from": "deferred", "to": "done"},
        "reason": f"marker_closed {marker_id}: {resolution}",
        "task_id": None,
        "session_id": _session_id(),
        "plan_slug": source_plan if source_plan else "scheduled_markers",
        "mode_at_transition": {"overnight": False, "guardrail": True},
    }
    _append(target_dir, row, sub_emitter)


def emit_marker_create_refused(
    plan_dir: Optional[Path],
    refusal_code: str,
    refusal_message: str,
    marker_args: Optional[dict] = None,
    source_plan: Optional[str] = None,
    sub_emitter: str = EMIT_CREATE,
) -> None:
    target_dir = _resolve_plan_dir(plan_dir, source_plan)
    row = {
        "transition": {"from": "ready", "to": "blocked"},
        "reason": (
            f"marker_create_refused [{refusal_code}]: {refusal_message}"
        ),
        "task_id": None,
        "session_id": _session_id(),
        "plan_slug": source_plan if source_plan else "scheduled_markers",
        "mode_at_transition": {"overnight": False, "guardrail": True},
    }
    _append(target_dir, row, sub_emitter)


def emit_prefix_registered(
    plan_dir: Optional[Path],
    prefix: str,
    domain: str,
    owner: str,
    sub_emitter: str = EMIT_REGISTER_PREFIX,
) -> None:
    target_dir = _resolve_plan_dir(plan_dir, None)
    row = {
        "transition": {"from": "ready", "to": "done"},
        "reason": (
            f"prefix_registered {prefix}: domain={domain}; owner={owner}"
        ),
        "task_id": None,
        "session_id": _session_id(),
        "plan_slug": "scheduled_markers",
        "mode_at_transition": {"overnight": False, "guardrail": True},
    }
    _append(target_dir, row, sub_emitter)


def _append(plan_dir: Path, row: dict, sub_emitter: str) -> None:
    """Final hop: call into §C writer (or its mock in tests).

    Narrows the §C-absent fallback (ImportError / RuntimeError from the
    top-level fallback stub) per §K mid-section review observation 1.
    Other exceptions (e.g., InvalidTransitionError, schema rejection,
    flock failure) re-raise so structural row bugs surface instead of
    silently dropping log emissions.
    """
    try:
        append_row(plan_dir, row, sub_emitter)
    except (ImportError, RuntimeError) as exc:
        # Only swallow §C-absent conditions; structural bugs must surface.
        import sys
        print(
            f"WARN: marker log emit skipped — §C writer unavailable "
            f"(sub_emitter={sub_emitter}; cause={type(exc).__name__}); "
            f"continuing.",
            file=sys.stderr,
        )


def _resolve_plan_dir(plan_dir: Optional[Path], source_plan: Optional[str]) -> Path:
    """Resolve the directory that hosts `_orchestrator_log.jsonl` for this event.

    Precedence:
      1. Explicit `plan_dir` arg if provided (used by route-marker wrapper)
      2. `docs/plans/<source_plan>/` if `source_plan` looks like a real slug
      3. `docs/plans/scheduled_markers/` fallback
    """
    if plan_dir is not None:
        return Path(plan_dir)
    repo_root = _repo_root()
    if source_plan and source_plan != "null":
        candidate = repo_root / "docs" / "plans" / source_plan
        if candidate.is_dir():
            return candidate
    return repo_root / "docs" / "plans" / "scheduled_markers"


def _session_id() -> str:
    """Read $CLAUDE_SESSION_ID, else stub a placeholder."""
    return os.environ.get("CLAUDE_SESSION_ID") or "sess_00000000"


def _repo_root() -> Path:
    """Resolve repo root by walking up from this file."""
    # bin/_marker/log_emit.py → bin/_marker → bin → REPO_ROOT
    return Path(__file__).resolve().parents[2]
