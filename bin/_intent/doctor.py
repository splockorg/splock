"""`bin/intent doctor` (implplan §P.impl.7).

Algorithm:
  1. SELECT all rows from agent_sessions WHERE closed_at IS NULL.
  2. For each active session, parse closure_trigger; dispatch to detector.
  3. If detector returns satisfied: invoke `bin/intent complete <session_id>`
     subprocess; emit `intent.complete` marker.
  4. Emit `intent.sync_resolved` for any local-JSONL row whose MySQL sync
     was pending.

Per P.impl.5 MINOR-1: reconcile collision_blocked local rows to
closed_at=<ts> + closure_trigger="collision_blocked" (no MySQL sync ever).
"""

from __future__ import annotations

import datetime
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import closure_triggers, db, jsonl_writer, markers
from .exit_codes import EXIT_ATOMIC_WRITE_FAILED, EXIT_OK

logger = logging.getLogger(__name__)


def _now_iso_z() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def run(
    *,
    dry_run: bool = False,
    json_output: bool = False,
    reconcile_sync_pending: bool = True,
    stdout=None,
    stderr=None,
    emitted_by: str = "bin/intent:doctor",
) -> int:
    out = stdout or sys.stdout

    summary: dict[str, list] = {
        "closed_by_trigger": [],
        "collision_blocked_reconciled": [],
        "sync_pending_resolved": [],
        "sync_pending_remaining": [],
        "errors": [],
    }

    # --- Step A: local-JSONL reconciliation for collision_blocked ---
    local_rows = jsonl_writer.read_all()
    mutated = False
    for r in local_rows:
        if r.get("collision_outcome") == "blocked" and not r.get("closed_at"):
            r["closed_at"] = _now_iso_z()
            r["closure_trigger"] = "collision_blocked"
            r["sync_pending"] = False
            summary["collision_blocked_reconciled"].append(r.get("session_id"))
            mutated = True

    # --- Step B: MySQL-side closure-trigger sweep ---
    try:
        with db.serializable_transaction() as (_conn, cursor):
            cursor.execute(
                """
                SELECT session_id, closure_trigger, last_activity_at
                FROM extraction.agent_sessions
                WHERE closed_at IS NULL
                """
            )
            active = list(cursor.fetchall())
        for row in active:
            sid = row.get("session_id")
            trigger = row.get("closure_trigger") or ""
            try:
                spec = closure_triggers.parse(trigger)
            except (
                closure_triggers.MalformedClosureTriggerError,
                closure_triggers.OpenEndedClosureTriggerError,
            ):
                continue
            last = row.get("last_activity_at")
            if isinstance(last, str):
                try:
                    last_dt = datetime.datetime.fromisoformat(last.replace("Z", "+00:00"))
                except ValueError:
                    last_dt = None
            elif isinstance(last, datetime.datetime):
                last_dt = last
            else:
                last_dt = None
            if not closure_triggers.is_satisfied(spec, last_activity_at=last_dt):
                continue
            if dry_run:
                summary["closed_by_trigger"].append(
                    {"session_id": sid, "trigger": trigger, "dry_run": True}
                )
                continue
            # Invoke bin/intent complete subprocess.
            repo_root = Path(__file__).resolve().parents[2]
            bin_intent = repo_root / "bin" / "intent"
            try:
                completed = subprocess.run(
                    [
                        str(bin_intent),
                        "complete",
                        sid,
                        "--reason",
                        f"doctor: {spec.shape} condition met",
                    ],
                    cwd=str(repo_root),
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                if completed.returncode == 0:
                    summary["closed_by_trigger"].append(
                        {"session_id": sid, "trigger": trigger}
                    )
                else:
                    summary["errors"].append(
                        {
                            "session_id": sid,
                            "trigger": trigger,
                            "exit": completed.returncode,
                            "stderr": completed.stderr.decode("utf-8", errors="replace"),
                        }
                    )
            except (OSError, subprocess.TimeoutExpired) as exc:
                summary["errors"].append({"session_id": sid, "exc": str(exc)})
    except db.MySQLUnavailable as exc:
        summary["errors"].append({"mysql": str(exc)})

    # --- Step C: sync_pending reconciliation ---
    if reconcile_sync_pending:
        for r in local_rows:
            if not r.get("sync_pending"):
                continue
            if r.get("collision_outcome") == "blocked":
                continue
            sid = r.get("session_id")
            if dry_run:
                summary["sync_pending_resolved"].append(
                    {"session_id": sid, "dry_run": True}
                )
                continue
            try:
                with db.serializable_transaction() as (conn, cursor):
                    existing = db.select_session(cursor, sid)
                    if existing is None:
                        db_payload = dict(r)
                        db_payload.pop("sync_pending", None)
                        db_payload.pop("sync_pending_error", None)
                        db_payload.pop("collision_outcome", None)
                        if isinstance(db_payload.get("claimed_paths"), list):
                            db_payload["claimed_paths"] = json.dumps(
                                db_payload["claimed_paths"], sort_keys=True
                            )
                        db.insert_session(cursor, db_payload)
                        conn.commit()
                    r["sync_pending"] = False
                    summary["sync_pending_resolved"].append({"session_id": sid})
                    mutated = True
                    markers.emit(
                        markers.EVENT_SYNC_RESOLVED,
                        {
                            "session_id": sid,
                            "resolved_at": _now_iso_z(),
                            "attempts": 1,
                        },
                        emitted_by=emitted_by,
                        session_id=sid,
                    )
            except db.MySQLUnavailable as exc:
                summary["sync_pending_remaining"].append(
                    {"session_id": sid, "reason": str(exc)}
                )

    if mutated and not dry_run:
        try:
            jsonl_writer.rewrite_all(local_rows)
        except OSError as exc:
            (stderr or sys.stderr).write(
                json.dumps({"error": "atomic_write_failed", "detail": str(exc)}) + "\n"
            )
            return EXIT_ATOMIC_WRITE_FAILED

    if json_output:
        out.write(json.dumps(summary, sort_keys=True, ensure_ascii=False) + "\n")
    else:
        out.write(
            f"doctor: closed_by_trigger={len(summary['closed_by_trigger'])}; "
            f"collision_blocked_reconciled={len(summary['collision_blocked_reconciled'])}; "
            f"sync_pending_resolved={len(summary['sync_pending_resolved'])}; "
            f"sync_pending_remaining={len(summary['sync_pending_remaining'])}; "
            f"errors={len(summary['errors'])}\n"
        )
    return EXIT_OK


__all__ = ["run"]
