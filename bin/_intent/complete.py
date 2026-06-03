"""`bin/intent complete <session_id>` (implplan §P.impl.3).

Marks session closed_at = NOW(), emits `intent.complete` marker.
Exit 41 if session_id not found OR already terminal.
"""

from __future__ import annotations

import datetime
import json
import sys
from typing import Optional

from . import db, jsonl_writer, markers, refusal
from .exit_codes import (
    EXIT_ATOMIC_WRITE_FAILED,
    EXIT_INTENT_SESSION_NOT_FOUND,
    EXIT_OK,
)


def _now_iso_z() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def run(
    *,
    session_id: str,
    reason: Optional[str] = None,
    emitted_by: str = "bin/intent:complete",
    dry_run: bool = False,
    json_output: bool = False,
    stdout=None,
    stderr=None,
    closure_trigger_satisfied: Optional[str] = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr

    try:
        refusal.validate_emitted_by(emitted_by)
    except refusal.EnumViolation as exc:
        err.write(json.dumps({"error": "enum_violation", "detail": str(exc)}) + "\n")
        return EXIT_OK  # caller invoked with bad emitted_by; structurally usage

    ts = _now_iso_z()

    if dry_run:
        out.write(
            json.dumps(
                {"dry_run": True, "session_id": session_id, "would_close_at": ts},
                sort_keys=True,
            )
            + "\n"
        )
        return EXIT_OK

    # Local JSONL: find + mutate.
    rows = jsonl_writer.read_all()
    found_local = False
    for r in rows:
        if r.get("session_id") == session_id:
            found_local = True
            if r.get("closed_at"):
                err.write(
                    json.dumps(
                        {
                            "error": "intent_session_not_found",
                            "detail": f"{session_id} already closed at {r.get('closed_at')}",
                        }
                    )
                    + "\n"
                )
                return EXIT_INTENT_SESSION_NOT_FOUND
            r["closed_at"] = ts
            r["status"] = "Done"
            r["last_activity_at"] = ts
            if reason:
                r["complete_reason"] = reason
    try:
        jsonl_writer.rewrite_all(rows)
    except OSError as exc:
        err.write(json.dumps({"error": "atomic_write_failed", "detail": str(exc)}) + "\n")
        return EXIT_ATOMIC_WRITE_FAILED

    found_db = False
    try:
        with db.serializable_transaction() as (conn, cursor):
            row = db.select_session(cursor, session_id)
            if row is not None:
                if row.get("closed_at"):
                    err.write(
                        json.dumps(
                            {
                                "error": "intent_session_not_found",
                                "detail": f"{session_id} already closed",
                            }
                        )
                        + "\n"
                    )
                    return EXIT_INTENT_SESSION_NOT_FOUND
                found_db = True
                db.update_session(
                    cursor, session_id,
                    {"closed_at": ts, "status": "Done", "last_activity_at": ts},
                )
                conn.commit()
    except db.MySQLUnavailable:
        pass

    if not found_local and not found_db:
        err.write(
            json.dumps(
                {
                    "error": "intent_session_not_found",
                    "detail": f"no row for session_id={session_id}",
                }
            )
            + "\n"
        )
        return EXIT_INTENT_SESSION_NOT_FOUND

    markers.emit(
        markers.EVENT_COMPLETE,
        {
            "session_id": session_id,
            "closed_at": ts,
            "reason": reason,
            "closure_trigger_satisfied": closure_trigger_satisfied,
        },
        emitted_by=emitted_by,
        session_id=session_id,
    )

    if json_output:
        out.write(
            json.dumps(
                {"ok": True, "session_id": session_id, "closed_at": ts}, sort_keys=True
            )
            + "\n"
        )
    else:
        out.write(f"completed {session_id} at {ts}\n")
    return EXIT_OK


__all__ = ["run"]
