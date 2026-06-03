"""Stdout JSON marker emission (implplan §P.impl.11).

Six marker shapes per the table at lines 8917-8924. Each emission
double-emits:
  1. Stdout JSON line (one object per line, mirroring gate-event pattern).
  2. INSERT into `extraction.intent_event_log` (when MySQL reachable).
  3. When `SPLOCK_CHAIN_ID` is set, co-emit to `_orchestrator_log.jsonl`
     via `bin/_jsonl_log/writer.append_row` with the new event_type
     value (intent_register / intent_collision / intent_update /
     intent_complete / intent_sync_pending / intent_sync_resolved).

`emit()` validates `emitted_by` via `refusal.validate_emitted_by(...)`;
the validation reads `refusal.EMITTED_BY` transitively, so any allowlist
extension lands here automatically. T2 (intent_session_auto_register)
extended that allowlist with `session_start_auto` — bumping
KNOWN_WRITERS to v6. The full v6 surface is enumerated in writers.py.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import socket
import sys
from pathlib import Path
from typing import Any, Optional

from . import db, refusal

logger = logging.getLogger(__name__)


EVENT_REGISTER = "intent.register"
EVENT_COLLISION = "intent.collision"
EVENT_UPDATE = "intent.update"
EVENT_COMPLETE = "intent.complete"
EVENT_SYNC_PENDING = "intent.sync_pending"
EVENT_SYNC_RESOLVED = "intent.sync_resolved"

# Map intent.* marker names → §C event_type closed-enum values (writers.py).
EVENT_TYPE_MAP = {
    EVENT_REGISTER: "intent_register",
    EVENT_COLLISION: "intent_collision",
    EVENT_UPDATE: "intent_update",
    EVENT_COMPLETE: "intent_complete",
    EVENT_SYNC_PENDING: "intent_sync_pending",
    EVENT_SYNC_RESOLVED: "intent_sync_resolved",
}


def _now_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _hostname() -> str:
    return socket.gethostname()


def emit(
    event: str,
    payload: dict[str, Any],
    *,
    emitted_by: str,
    session_id: Optional[str] = None,
    collision_id: Optional[str] = None,
    mysql_conn=None,
    stdout=None,
) -> None:
    """Emit one marker.

    Always writes stdout. Best-effort INSERT to intent_event_log; logs +
    swallows failures. When SPLOCK_CHAIN_ID is set in env, also co-emit to
    `_orchestrator_log.jsonl`.
    """
    refusal.validate_event(event)
    refusal.validate_emitted_by(emitted_by)

    ts = _now_iso()
    host = _hostname()

    row = {
        "event": event,
        "ts": ts,
        "host": host,
        "emitted_by": emitted_by,
    }
    if session_id:
        row["session_id"] = session_id
    if collision_id:
        row["collision_id"] = collision_id
    # Merge payload after, but never let payload override structural keys.
    for k, v in payload.items():
        if k not in row:
            row[k] = v

    line = json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n"
    (stdout or sys.stdout).write(line)
    try:
        (stdout or sys.stdout).flush()
    except Exception:  # noqa: BLE001
        pass

    # Best-effort DB log.
    try:
        _insert_event_log_row(
            event=event,
            session_id=session_id,
            collision_id=collision_id,
            payload=row,
            emitted_by=emitted_by,
            host=host,
            ts=ts,
            mysql_conn=mysql_conn,
        )
    except db.MySQLUnavailable:
        pass
    except Exception as exc:  # noqa: BLE001 — observability is fail-open
        sys.stderr.write(
            f"warning: intent_event_log insert skipped: {exc}\n"
        )

    # Chain-context co-emission to _orchestrator_log.jsonl.
    chain_id = os.environ.get("SPLOCK_CHAIN_ID")
    if chain_id:
        _coemit_orchestrator_log(
            event=event,
            payload=row,
            emitted_by=emitted_by,
            session_id=session_id,
            collision_id=collision_id,
        )


def _insert_event_log_row(
    *,
    event: str,
    session_id: Optional[str],
    collision_id: Optional[str],
    payload: dict,
    emitted_by: str,
    host: str,
    ts: str,
    mysql_conn,
) -> None:
    """INSERT one row into extraction.intent_event_log. Raises on failure."""
    own = mysql_conn is None
    if own:
        mysql_conn = db.connection()
    cursor = mysql_conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO extraction.intent_event_log
                (event, session_id, collision_id, payload, emitted_by,
                 host, emitted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event,
                session_id,
                collision_id,
                json.dumps(payload, sort_keys=True, ensure_ascii=False),
                emitted_by,
                host,
                ts,
            ),
        )
        mysql_conn.commit()
    finally:
        try:
            cursor.close()
        except Exception:  # noqa: BLE001
            pass
        if own:
            try:
                mysql_conn.close()
            except Exception:  # noqa: BLE001
                pass


def _coemit_orchestrator_log(
    *,
    event: str,
    payload: dict,
    emitted_by: str,
    session_id: Optional[str],
    collision_id: Optional[str],
) -> None:
    """When SPLOCK_CHAIN_ID is set, also write a §C-shape row to
    docs/plans/<slug>/_orchestrator_log.jsonl.

    Failure is best-effort (logged, not raised) per §P.impl.11 observability.
    """
    slug = os.environ.get("SPLOCK_PLAN_SLUG")
    if not slug:
        return
    repo_root = Path(__file__).resolve().parents[2]
    plan_dir = repo_root / "docs" / "plans" / slug
    if not plan_dir.is_dir():
        return
    event_type = EVENT_TYPE_MAP.get(event)
    if event_type is None:
        return
    chain_id = os.environ.get("SPLOCK_CHAIN_ID")
    row = {
        "transition": {"from": "ready", "to": "ready"},
        "event_type": event_type,
        "reason": f"{event} payload={json.dumps(payload, sort_keys=True)}",
        "task_id": None,
        "session_id": session_id or "sess_00000000",
        "plan_slug": slug,
        "chain_id": chain_id,
        "mode_at_transition": {"overnight": True, "guardrail": False},
    }
    if collision_id:
        row["collision_id"] = collision_id
    try:
        from bin._jsonl_log.writer import append_row
        append_row(plan_dir, row, emitted_by=emitted_by)
    except Exception as exc:  # noqa: BLE001 — observability fail-open
        sys.stderr.write(
            f"warning: orchestrator_log co-emit skipped: {exc}\n"
        )


__all__ = [
    "EVENT_REGISTER",
    "EVENT_COLLISION",
    "EVENT_UPDATE",
    "EVENT_COMPLETE",
    "EVENT_SYNC_PENDING",
    "EVENT_SYNC_RESOLVED",
    "EVENT_TYPE_MAP",
    "emit",
]
