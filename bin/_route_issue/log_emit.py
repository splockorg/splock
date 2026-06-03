"""Thin delegate to `bin/_jsonl_log/writer.append_row` (implplan §L.impl + §C.impl.6).

Mirrors the `bin/_marker/log_emit.py` + `bin/_update_orchestrator/log_emit.py`
pattern. Tests mock `bin._route_issue.log_emit.append_row`.

Sub-emitter constants (parallels §K.impl.3 attribution stamping per §L.impl.3):

  EMIT_BASE          = "bin/route_issue"
  EMIT_FIX_NOW       = "bin/route_issue:fix-now"
  EMIT_OUTSTANDING   = "bin/route_issue:outstanding"
  EMIT_MARKER        = "bin/route_issue:marker"
  EMIT_TIER_PROMOTE  = "bin/route_issue:tier-promote"
  EMIT_ESCALATE      = "bin/route_issue:escalate"
  EMIT_LAZY_DUMP     = "bin/lazy-dump-check"

All seven values are pre-registered in `bin/_jsonl_log/writers.py`
`KNOWN_WRITERS`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# Top-level import; tests mock via `mock.patch("bin._route_issue.log_emit.append_row")`.
try:
    from bin._jsonl_log.writer import append_row  # noqa: F401
except ImportError:  # pragma: no cover — only hit during isolated unit-test boot
    def append_row(plan_dir, row, emitted_by):  # type: ignore[no-redef]
        raise RuntimeError(
            "bin/_jsonl_log/writer.py not yet shipped (§C dependency). "
            "Tests should mock `bin._route_issue.log_emit.append_row`."
        )


EMIT_BASE = "bin/route_issue"
EMIT_FIX_NOW = "bin/route_issue:fix-now"
EMIT_OUTSTANDING = "bin/route_issue:outstanding"
EMIT_MARKER = "bin/route_issue:marker"
EMIT_TIER_PROMOTE = "bin/route_issue:tier-promote"
EMIT_ESCALATE = "bin/route_issue:escalate"
EMIT_LAZY_DUMP = "bin/lazy-dump-check"


def session_id() -> str:
    """Read `$CLAUDE_SESSION_ID`, else stub a placeholder."""
    return os.environ.get("CLAUDE_SESSION_ID") or "sess_00000000"


def emit_row(
    plan_dir: Path,
    plan_slug: str,
    transition_from: str,
    transition_to: str,
    reason: str,
    *,
    emitted_by: str,
    task_id: Optional[str] = None,
    extra: Optional[dict] = None,
) -> None:
    """Emit one transition row via the §C shared writer.

    Mirrors `bin._marker.log_emit` payload shape; `event_type` rides on
    `extra` per §L.impl.11 (additive payload, §C row schema's
    `additionalProperties` policy permits it).
    """
    row: dict = {
        "transition": {"from": transition_from, "to": transition_to},
        "reason": reason,
        "task_id": task_id,
        "session_id": session_id(),
        "plan_slug": plan_slug,
        "mode_at_transition": {"overnight": False, "guardrail": True},
    }
    if extra:
        row.update(extra)
    try:
        append_row(plan_dir, row, emitted_by)
    except (ImportError, RuntimeError) as exc:
        print(
            f"WARN: route_issue log emit skipped — §C writer unavailable "
            f"(sub_emitter={emitted_by}; cause={type(exc).__name__}); "
            f"continuing.",
            file=sys.stderr,
        )


def resolve_plan_dir(plan_dir: Optional[Path], plan_slug: Optional[str]) -> Path:
    """Resolve target dir for `_orchestrator_log.jsonl`.

    Same precedence as `bin._marker.log_emit._resolve_plan_dir`:
      1. Explicit `plan_dir`
      2. `docs/plans/<plan_slug>/` if directory exists
      3. `docs/plans/splock/` fallback (route_issue is a
         splock-substrate CLI; this is its home slug)
    """
    if plan_dir is not None:
        return Path(plan_dir)
    root = _repo_root()
    if plan_slug and plan_slug not in (None, "null"):
        cand = root / "docs" / "plans" / plan_slug
        if cand.is_dir():
            return cand
    return root / "docs" / "plans" / "splock"


def _repo_root() -> Path:
    # bin/_route_issue/log_emit.py → bin/_route_issue → bin → REPO_ROOT
    return Path(__file__).resolve().parents[2]
