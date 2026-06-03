"""`bin/intent check` — read-only collision query (implplan §P.impl.3).

Exit 0 on no-match, exit 40 on match. NEVER writes collision_log row
(read-only); distinguished from `register` exit 40 which writes the row
+ emits intent.collision marker.
"""

from __future__ import annotations

import json
import socket
import sys
from typing import Optional

from . import db, jsonl_writer, register as register_mod
from .exit_codes import EXIT_INTENT_COLLISION_DETECTED, EXIT_OK


def run(
    *,
    area: str,
    paths: Optional[list[str]] = None,
    host: Optional[str] = None,
    json_output: bool = False,
    stdout=None,
    stderr=None,
) -> int:
    out = stdout or sys.stdout
    paths = paths or []

    matches: list[dict] = []

    # Local-JSONL pass (host-scoped or all).
    rows = jsonl_writer.read_all()
    target_host = host or socket.gethostname()
    for r in rows:
        if r.get("closed_at"):
            continue
        if r.get("host") != target_host and host is not None:
            continue
        if r.get("target_system_area") == area:
            matches.append(r)
            continue
        cps = r.get("claimed_paths") or []
        if isinstance(cps, str):
            try:
                cps = json.loads(cps)
            except json.JSONDecodeError:
                cps = []
        for cp in cps:
            if register_mod._globs_overlap(cp, paths):
                matches.append(r)
                break

    # MySQL pass (best-effort).
    try:
        with db.serializable_transaction() as (_conn, cursor):
            paths_json = json.dumps(paths, sort_keys=True, ensure_ascii=False)
            db_matches = db.select_overlapping_for_update(cursor, area, paths_json)
            db_filtered = register_mod.filter_python_glob_overlap(db_matches, paths) if paths else db_matches
            for m in db_filtered:
                m_sid = m.get("session_id")
                if not any(x.get("session_id") == m_sid for x in matches):
                    matches.append(m)
    except db.MySQLUnavailable:
        pass

    if matches:
        payload = {
            "collision": True,
            "area": area,
            "paths": paths,
            "host": target_host if host is not None else None,
            "matches": [
                {
                    "session_id": m.get("session_id"),
                    "kind": m.get("kind"),
                    "target_system_area": m.get("target_system_area"),
                    "host": m.get("host"),
                }
                for m in matches
            ],
        }
        if json_output:
            out.write(json.dumps(payload, sort_keys=True) + "\n")
        else:
            out.write(
                f"intent_collision_detected: {len(matches)} active session(s) "
                f"on area={area}\n"
            )
            for m in matches:
                out.write(
                    f"  - {m.get('session_id')} kind={m.get('kind')} "
                    f"host={m.get('host')}\n"
                )
        return EXIT_INTENT_COLLISION_DETECTED

    if json_output:
        out.write(json.dumps({"ok": True, "area": area, "matches": []}) + "\n")
    else:
        out.write(f"no active sessions on area={area}\n")
    return EXIT_OK


__all__ = ["run"]
