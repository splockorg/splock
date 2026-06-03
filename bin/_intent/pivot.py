"""`bin/intent pivot <session_id> --area <new_ID>` (implplan §P.impl.3).

Pivot semantics: update session's `target_system_area` (and optionally
`claimed_paths`) to a new scope. Re-runs the collision check against the
new area; on collision, writes collision_log row + exits 40.

Exit codes: 0 ok / 1 usage / 7 atomic-write / 40 intent_collision_detected /
41 intent_session_not_found.
"""

from __future__ import annotations

import datetime
import json
import socket
import sys
from typing import Optional

from . import db, dispatch, jsonl_writer, markers, refusal, register as register_mod
from .exit_codes import (
    EXIT_ATOMIC_WRITE_FAILED,
    EXIT_INTENT_COLLISION_DETECTED,
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
    area: str,
    paths: Optional[list[str]] = None,
    emitted_by: str = "bin/intent:pivot",
    dry_run: bool = False,
    json_output: bool = False,
    stdout=None,
    stderr=None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr

    try:
        refusal.validate_emitted_by(emitted_by)
    except refusal.EnumViolation as exc:
        err.write(json.dumps({"error": "enum_violation", "detail": str(exc)}) + "\n")
        return EXIT_OK  # structurally usage

    ts = _now_iso_z()
    host = socket.gethostname()

    if dry_run:
        out.write(
            json.dumps(
                {"dry_run": True, "session_id": session_id, "new_area": area, "new_paths": paths},
                sort_keys=True,
            )
            + "\n"
        )
        return EXIT_OK

    # Local lookup for original paths.
    local = jsonl_writer.find_by_session_id(session_id)
    new_paths = paths if paths is not None else (
        local.get("claimed_paths") if local else []
    )
    if isinstance(new_paths, str):
        try:
            new_paths = json.loads(new_paths)
        except json.JSONDecodeError:
            new_paths = []

    # Local-side refusal first — when MySQL is down AND local is missing,
    # this is the only signal we have. Avoids spurious "success" exits.
    if local is None:
        # Try MySQL once before refusing definitively.
        try:
            with db.serializable_transaction() as (_conn, cursor):
                if db.select_session(cursor, session_id) is None:
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
        except db.MySQLUnavailable:
            err.write(
                json.dumps(
                    {
                        "error": "intent_session_not_found",
                        "detail": (
                            f"no local row for session_id={session_id} "
                            f"(MySQL unavailable for cross-check)"
                        ),
                    }
                )
                + "\n"
            )
            return EXIT_INTENT_SESSION_NOT_FOUND

    # SERIALIZABLE: collision check, then update or refuse.
    try:
        with db.serializable_transaction() as (conn, cursor):
            row = db.select_session(cursor, session_id)
            if row is None and local is None:
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
            if row is not None and row.get("closed_at"):
                err.write(
                    json.dumps(
                        {
                            "error": "intent_session_not_found",
                            "detail": f"{session_id} closed",
                        }
                    )
                    + "\n"
                )
                return EXIT_INTENT_SESSION_NOT_FOUND

            paths_json = json.dumps(new_paths, sort_keys=True, ensure_ascii=False)
            matches = db.select_overlapping_for_update(cursor, area, paths_json)
            # Filter out the session being pivoted itself.
            matches = [m for m in matches if m.get("session_id") != session_id]
            colliding = register_mod.filter_python_glob_overlap(matches, list(new_paths))
            if colliding:
                collision_id = register_mod.new_collision_id()
                mode = "autonomous" if dispatch.is_autonomous_context(None) else "interactive"
                lineage = [
                    {
                        "session_id": m.get("session_id"),
                        "kind": m.get("kind"),
                        "target_system_area": m.get("target_system_area"),
                        "host": m.get("host"),
                    }
                    for m in colliding
                ]
                db.insert_collision(
                    cursor,
                    {
                        "collision_id": collision_id,
                        "colliding_session_id": session_id,
                        "colliding_area": area,
                        "lineage_snapshot": json.dumps(
                            lineage, sort_keys=True, ensure_ascii=False
                        ),
                        "dispatch_mode": mode,
                        "resolution": None,
                        "resolution_at": None,
                        "detected_at": ts,
                        "host": host,
                    },
                )
                conn.commit()
                markers.emit(
                    markers.EVENT_COLLISION,
                    {
                        "collision_id": collision_id,
                        "colliding_session_id": session_id,
                        "colliding_area": area,
                        "lineage_snapshot": lineage,
                        "dispatch_mode": mode,
                        "trigger": "pivot",
                    },
                    emitted_by=emitted_by,
                    session_id=session_id,
                    collision_id=collision_id,
                    mysql_conn=conn,
                )
                dispatch.handle_collision(
                    mode=mode,
                    collision_id=collision_id,
                    colliding_session_id=session_id,
                    colliding_area=area,
                    lineage_snapshot=lineage,
                    plan_slug=None,
                    chain_id=None,
                    stderr=err,
                )
                return EXIT_INTENT_COLLISION_DETECTED

            if row is not None:
                db.update_session(
                    cursor, session_id,
                    {
                        "target_system_area": area,
                        "claimed_paths": paths_json,
                        "last_activity_at": ts,
                    },
                )
                conn.commit()
    except db.MySQLUnavailable:
        pass

    # Local update (always; even on MySQL outage).
    rows = jsonl_writer.read_all()
    for r in rows:
        if r.get("session_id") == session_id:
            r["target_system_area"] = area
            r["claimed_paths"] = list(new_paths)
            r["last_activity_at"] = ts
    try:
        jsonl_writer.rewrite_all(rows)
    except OSError as exc:
        err.write(json.dumps({"error": "atomic_write_failed", "detail": str(exc)}) + "\n")
        return EXIT_ATOMIC_WRITE_FAILED

    markers.emit(
        markers.EVENT_UPDATE,
        {
            "session_id": session_id,
            "delta": {"target_system_area": area, "claimed_paths": list(new_paths)},
            "last_activity_at": ts,
            "trigger": "pivot",
        },
        emitted_by=emitted_by,
        session_id=session_id,
    )

    if json_output:
        out.write(
            json.dumps(
                {"ok": True, "session_id": session_id, "new_area": area},
                sort_keys=True,
            )
            + "\n"
        )
    else:
        out.write(f"pivoted {session_id} to area={area}\n")
    return EXIT_OK


__all__ = ["run"]
