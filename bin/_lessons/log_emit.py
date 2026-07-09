"""Thin delegate to `bin/_jsonl_log/writer.append_row` for §M lessons events.

Per implplan §M.impl.5 step 9: each `bin/lessons` invocation emits a
row to `_orchestrator_log.jsonl` via the §C shared writer.

Event types:

- ``lesson_added`` — emitted by ``bin/lessons:add`` on successful
  append. ``transition: {from: ready, to: done}``.
- ``lesson_queried`` — emitted by ``bin/lessons:query`` on every
  invocation. ``transition: {from: ready, to: done}``.

The §C `event_type` field is carried in the row payload (not in the
top-level row schema); §C's row schema treats it as an additive
payload key, consistent with the §H + §K + §L emitter patterns.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from . import EMIT_ADD, EMIT_QUERY


try:
    from bin._jsonl_log.writer import append_row  # noqa: F401
except ImportError:  # pragma: no cover — only hit if §C not yet shipped
    def append_row(plan_dir, row, emitted_by):  # type: ignore[no-redef]
        raise RuntimeError(
            "bin/_jsonl_log/writer.py not available (§C dependency). "
            "Tests should mock `bin._lessons.log_emit.append_row`."
        )


def _session_id() -> str:
    return os.environ.get("CLAUDE_SESSION_ID") or "sess_00000000"


def _resolve_plan_dir(slug: str, *, plans_dir: Optional[Path] = None) -> Path:
    if plans_dir is not None:
        return plans_dir / slug
    # Resolved at CALL time, not import time: the adopter root depends on the
    # invoking directory, and this module is imported before the CLI has run.
    from bin._env_paths import plans_dir as _env_paths_plans_dir

    return _env_paths_plans_dir() / slug


def emit_lesson_added(
    slug: str,
    *,
    task_id: str,
    title: str,
    plans_dir: Optional[Path] = None,
    sub_emitter: str = EMIT_ADD,
) -> None:
    """Emit a `lesson_added` event row to the slug's JSONL."""
    plan_dir = _resolve_plan_dir(slug, plans_dir=plans_dir)
    row = {
        "event_type": "lesson_added",
        "transition": {"from": "ready", "to": "done"},
        "reason": f"lesson_added {task_id}: {title}",
        "task_id": task_id,
        "session_id": _session_id(),
        "plan_slug": slug,
        "mode_at_transition": {"overnight": False, "guardrail": True},
    }
    _safe_append(plan_dir, row, sub_emitter)


def emit_lesson_queried(
    slug: str,
    *,
    task: Optional[str],
    keyword: Optional[str],
    hits: int,
    plans_dir: Optional[Path] = None,
    sub_emitter: str = EMIT_QUERY,
) -> None:
    """Emit a `lesson_queried` event row to the slug's JSONL."""
    plan_dir = _resolve_plan_dir(slug, plans_dir=plans_dir)
    reason = (
        f"lesson_queried task={task or '*'} "
        f"keyword={keyword or '*'} hits={hits}"
    )
    row = {
        "event_type": "lesson_queried",
        "transition": {"from": "ready", "to": "done"},
        "reason": reason,
        "task_id": task,
        "session_id": _session_id(),
        "plan_slug": slug,
        "mode_at_transition": {"overnight": False, "guardrail": True},
    }
    _safe_append(plan_dir, row, sub_emitter)


def _safe_append(plan_dir: Path, row: dict, sub_emitter: str) -> None:
    """Final hop. Swallow §C-absent / plan-dir-missing conditions.

    Mirrors `bin._marker.log_emit._append` narrow-swallow discipline:
    only ImportError / RuntimeError (§C unavailable) and FileNotFoundError
    (plan dir doesn't exist) are swallowed; schema/transition errors
    re-raise so structural bugs surface.
    """
    if not plan_dir.exists():
        # Plan dir not present (e.g., test fixture). Skip emission rather
        # than auto-creating — emission is a best-effort forensic side
        # effect, not core CLI semantics.
        return
    try:
        append_row(plan_dir, row, sub_emitter)
    except (ImportError, RuntimeError) as exc:
        import sys
        print(
            f"WARN: lessons log emit skipped — §C writer unavailable "
            f"(sub_emitter={sub_emitter}; cause={type(exc).__name__}); "
            f"continuing.",
            file=sys.stderr,
        )


__all__ = [
    "emit_lesson_added",
    "emit_lesson_queried",
]
