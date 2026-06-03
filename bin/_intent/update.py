"""`bin/intent update <session_id>` (implplan §P.impl.3).

Optional flags: --paths --status --note. Exit 41 if session_id not
found OR already terminal (closed-session updates refused). Exit 2 on
enum violation.
"""

from __future__ import annotations

import datetime
import json
import socket
import sys
from typing import Optional

from . import db, jsonl_writer, markers, refusal
from .exit_codes import (
    EXIT_ATOMIC_WRITE_FAILED,
    EXIT_ENUM_VIOLATION,
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
    paths: Optional[list[str]] = None,
    status: Optional[str] = None,
    note: Optional[str] = None,
    emitted_by: str = "bin/intent:update",
    dry_run: bool = False,
    json_output: bool = False,
    stdout=None,
    stderr=None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr

    try:
        refusal.validate_emitted_by(emitted_by)
        if status is not None:
            refusal.validate_status(status)
    except refusal.EnumViolation as exc:
        err.write(json.dumps({"error": "enum_violation", "detail": str(exc)}) + "\n")
        return EXIT_ENUM_VIOLATION

    ts = _now_iso_z()
    delta: dict = {"last_activity_at": ts}
    if paths is not None:
        delta["claimed_paths"] = paths
    if status is not None:
        delta["status"] = status

    if dry_run:
        out.write(
            json.dumps(
                {"dry_run": True, "session_id": session_id, "delta": delta},
                sort_keys=True,
            )
            + "\n"
        )
        return EXIT_OK

    # Local JSONL update (find + rewrite).
    found_local = False
    rows = jsonl_writer.read_all()
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
            if paths is not None:
                r["claimed_paths"] = paths
            if status is not None:
                r["status"] = status
            if note:
                r["note"] = note
            r["last_activity_at"] = ts

    try:
        jsonl_writer.rewrite_all(rows)
    except OSError as exc:
        err.write(json.dumps({"error": "atomic_write_failed", "detail": str(exc)}) + "\n")
        return EXIT_ATOMIC_WRITE_FAILED

    # MySQL update (best-effort).
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
                                "detail": f"{session_id} already closed at {row.get('closed_at')}",
                            }
                        )
                        + "\n"
                    )
                    return EXIT_INTENT_SESSION_NOT_FOUND
                found_db = True
                db_delta = dict(delta)
                if "claimed_paths" in db_delta:
                    db_delta["claimed_paths"] = json.dumps(
                        db_delta["claimed_paths"], sort_keys=True
                    )
                db.update_session(cursor, session_id, db_delta)
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
        markers.EVENT_UPDATE,
        {
            "session_id": session_id,
            "delta": delta,
            "last_activity_at": ts,
            "note": note,
        },
        emitted_by=emitted_by,
        session_id=session_id,
    )

    if json_output:
        out.write(
            json.dumps(
                {"ok": True, "session_id": session_id, "delta": delta},
                sort_keys=True,
            )
            + "\n"
        )
    else:
        out.write(f"updated {session_id}\n")
    return EXIT_OK


__all__ = ["run"]
