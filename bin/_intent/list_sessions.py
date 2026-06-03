"""`bin/intent list` (implplan §P.impl.3).

Filterable by --area / --kind / --host / --active / --closed / --all.
Default scope: active. Exit 2 on enum violation; otherwise exit 0.
"""

from __future__ import annotations

import json
import socket
import sys
from typing import Optional

from . import db, jsonl_writer, refusal
from .exit_codes import EXIT_ENUM_VIOLATION, EXIT_OK


def run(
    *,
    area: Optional[str] = None,
    kind: Optional[str] = None,
    host: Optional[str] = None,
    active: bool = True,
    closed: bool = False,
    all_sessions: bool = False,
    json_output: bool = False,
    claude_session: Optional[str] = None,
    stdout=None,
    stderr=None,
) -> int:
    """List sessions, optionally filtered.

    `claude_session` (T1 — intent_session_auto_register): filter rows
    whose `claude_session_id` matches. Used to trace `/clear` continuations
    of the same upstream Claude Code session across multiple registry rows.
    Empty result + exit 0 when no rows match.
    """
    out = stdout or sys.stdout
    err = stderr or sys.stderr

    if kind is not None:
        try:
            refusal.validate_kind(kind)
        except refusal.EnumViolation as exc:
            err.write(json.dumps({"error": "enum_violation", "detail": str(exc)}) + "\n")
            return EXIT_ENUM_VIOLATION

    # Source of truth preference: MySQL when reachable; fall back to local.
    rows: list[dict] = []
    used_db = False
    try:
        with db.serializable_transaction() as (_conn, cursor):
            where = []
            params: list = []
            if all_sessions:
                pass
            elif closed:
                where.append("closed_at IS NOT NULL")
            else:
                where.append("closed_at IS NULL")
            if area:
                where.append("target_system_area = %s")
                params.append(area)
            if kind:
                where.append("kind = %s")
                params.append(kind)
            if host:
                where.append("host = %s")
                params.append(host)
            if claude_session:
                where.append("claude_session_id = %s")
                params.append(claude_session)
            sql = "SELECT * FROM extraction.agent_sessions"
            if where:
                sql += " WHERE " + " AND ".join(where)
            # T1: order by created_at desc — `started_at` is the closest
            # equivalent in this schema (DATETIME of session register).
            # Fall back to `last_activity_at` only when the test plan
            # explicitly references created_at; pre-T1 callers used the
            # latter for active-session triage. T1 spec wants
            # newest-first by registration time so /clear-continuation
            # lookups show the most recent row first.
            if claude_session:
                sql += " ORDER BY started_at DESC"
            else:
                sql += " ORDER BY last_activity_at DESC"
            cursor.execute(sql, tuple(params))
            rows = list(cursor.fetchall())
            used_db = True
    except db.MySQLUnavailable:
        pass

    if not used_db:
        local = jsonl_writer.read_all()
        for r in local:
            if not all_sessions:
                is_closed = bool(r.get("closed_at"))
                if closed and not is_closed:
                    continue
                if (not closed) and is_closed:
                    continue
            if area and r.get("target_system_area") != area:
                continue
            if kind and r.get("kind") != kind:
                continue
            if host and r.get("host") != host:
                continue
            if claude_session and r.get("claude_session_id") != claude_session:
                continue
            rows.append(r)
        # T1: when filtering by claude_session, sort newest-first by
        # `started_at` so the most recent /clear-continuation row leads.
        if claude_session:
            rows.sort(key=lambda r: r.get("started_at") or "", reverse=True)

    if json_output:
        # Cast non-JSON-safe values (datetime, bytes) to strings.
        out.write(json.dumps(_jsonify(rows), sort_keys=True, ensure_ascii=False) + "\n")
        return EXIT_OK

    if not rows:
        out.write("(no sessions)\n")
        return EXIT_OK
    for r in rows:
        sid = r.get("session_id")
        area_v = r.get("target_system_area")
        kind_v = r.get("kind")
        host_v = r.get("host")
        status = r.get("status")
        closed_at = r.get("closed_at") or "(active)"
        out.write(
            f"{sid}  area={area_v}  kind={kind_v}  host={host_v}  "
            f"status={status}  closed_at={closed_at}\n"
        )
    return EXIT_OK


def _jsonify(rows):
    out = []
    for r in rows:
        new_r = {}
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                new_r[k] = v.isoformat()
            elif isinstance(v, (bytes, bytearray)):
                new_r[k] = v.decode("utf-8", errors="replace")
            else:
                new_r[k] = v
        out.append(new_r)
    return out


__all__ = ["run"]
