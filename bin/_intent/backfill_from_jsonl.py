"""One-shot backfill — scan ~/.claude/projects/<project>/*.jsonl and
upsert any sessions missing from extraction.agent_sessions.

Use after Phase C install on each operator machine to import sessions
that started before the hooks were wired up. After that, hooks keep RDS
current and this script doesn't need to be run again.

Idempotent. Safe to re-run. Reuses one MySQL connection across all
writes (vs the per-call connect overhead of the hook fast-path) and
prints per-session progress.

Usage:
    source "$SPLOCK_VENV/bin/activate"
    python -m bin._intent.backfill_from_jsonl [--max-age-days 30]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import secrets
import socket
import sys
import time
from pathlib import Path

from bin._env_paths import load_env_file

load_env_file(Path(__file__).resolve().parent.parent.parent / ".env")

from . import db as _db  # noqa: E402
from . import hook_writer  # noqa: E402


def _project_dir() -> Path:
    cwd = os.path.abspath(os.getcwd())
    munged = "-" + cwd.lstrip("/").replace("/", "-")
    return Path.home() / ".claude" / "projects" / munged


def _ensure_row(cur, claude_session_id: str, jsonl_path: Path) -> str:
    """SELECT-or-INSERT, returning session_id PK.

    For backfill, started_at uses the jsonl's ctime (file creation), and
    last_activity_at uses the mtime (last write) — neither is `now`, so
    the historical timing is preserved. If a row already exists but its
    started_at looks like it came from a prior buggy backfill (within the
    last 24h), we correct it from the file ctime.
    """
    cur.execute(
        "SELECT session_id, started_at FROM extraction.agent_sessions "
        "WHERE claude_session_id = %s AND closed_at IS NULL "
        "ORDER BY started_at DESC LIMIT 1",
        (claude_session_id,),
    )
    row = cur.fetchone()
    try:
        stat = jsonl_path.stat()
        started_dt = datetime.datetime.fromtimestamp(stat.st_ctime, tz=datetime.timezone.utc)
        last_dt = datetime.datetime.fromtimestamp(stat.st_mtime, tz=datetime.timezone.utc)
    except OSError:
        started_dt = last_dt = datetime.datetime.now(datetime.timezone.utc)
    if row:
        existing_sid = row[0] if isinstance(row, tuple) else row["session_id"]
        existing_started = row[1] if isinstance(row, tuple) else row.get("started_at")
        # If the existing started_at is within the last 24h (likely a prior
        # buggy-backfill artifact), AND the file ctime is older, correct it.
        if isinstance(existing_started, datetime.datetime):
            existing_started_utc = (
                existing_started.replace(tzinfo=datetime.timezone.utc)
                if existing_started.tzinfo is None else existing_started
            )
            now = datetime.datetime.now(datetime.timezone.utc)
            looks_recent = (now - existing_started_utc).total_seconds() < 86400
            file_older = started_dt < existing_started_utc - datetime.timedelta(minutes=10)
            if looks_recent and file_older:
                cur.execute(
                    "UPDATE extraction.agent_sessions "
                    "SET started_at = %s, last_activity_at = %s WHERE session_id = %s",
                    (started_dt, last_dt, existing_sid),
                )
        return existing_sid
    sid_str = f"sess_{started_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}_{secrets.token_hex(2)}"
    host = socket.gethostname()
    cur.execute(
        """
        INSERT INTO extraction.agent_sessions
          (session_id, kind, target_system_area, claimed_paths,
           proposed_design_pattern, status, closure_trigger,
           originating_chain_id, originating_plan_slug,
           host, started_at, last_activity_at, closed_at,
           emitted_by, claude_session_id)
        VALUES
          (%s, 'interactive', 'unscoped_interactive', '["_unscoped"]',
           NULL, 'Planning', 'session_timeout:240m',
           NULL, NULL,
           %s, %s, %s, NULL,
           'user_prompt_submit_auto', %s)
        """,
        (sid_str, host, started_dt, last_dt, claude_session_id),
    )
    return sid_str


def _apply_signals_with_cursor(cur, session_id: str, signals: dict,
                                last_activity_dt: datetime.datetime) -> None:
    sets: list[str] = []
    params: list = []
    for col in ("custom_title", "git_branch", "workflow_stage"):
        v = signals.get(col)
        if v is not None:
            sets.append(f"{col} = %s"); params.append(v)
    for col in ("recent_prompts", "todo_state", "tools_used_count", "files_touched"):
        v = signals.get(col)
        if v is not None:
            sets.append(f"{col} = %s"); params.append(json.dumps(v))
    # Backfill: use the file's mtime, not NOW().
    sets.append("last_activity_at = %s"); params.append(last_activity_dt)
    if not sets:
        return
    params.append(session_id)
    cur.execute(
        "UPDATE extraction.agent_sessions SET " + ", ".join(sets) +
        " WHERE session_id = %s",
        tuple(params),
    )


def _upsert_subagent_with_cursor(cur, parent_cs: str, jsonl_path: Path) -> None:
    subagent_id = jsonl_path.stem
    meta_path = jsonl_path.with_suffix(".meta.json")
    agent_type = ""
    description = ""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        agent_type = (meta.get("agentType") or "").strip()[:40]
        description = (meta.get("description") or "").strip()[:500]
    except (OSError, json.JSONDecodeError):
        pass
    try:
        stat = jsonl_path.stat()
    except OSError:
        return
    last_activity = datetime.datetime.fromtimestamp(stat.st_mtime, tz=datetime.timezone.utc)
    data = hook_writer._read_tail(jsonl_path)
    counts = hook_writer._extract_tools_used_count(data) if data else {}
    files = hook_writer._extract_files_touched(data) if data else []
    cur.execute(
        """
        INSERT INTO extraction.agent_subagents
            (subagent_id, parent_claude_session_id, agent_type,
             description, started_at, last_activity_at, status,
             tools_used_count, files_touched)
        VALUES (%s, %s, %s, %s, %s, %s, 'done', %s, %s)
        ON DUPLICATE KEY UPDATE
            parent_claude_session_id = VALUES(parent_claude_session_id),
            agent_type    = COALESCE(VALUES(agent_type), agent_type),
            description   = COALESCE(VALUES(description), description),
            last_activity_at = VALUES(last_activity_at),
            tools_used_count = VALUES(tools_used_count),
            files_touched = VALUES(files_touched)
        """,
        (
            subagent_id,
            parent_cs,
            agent_type or None,
            description or None,
            last_activity,
            last_activity,
            json.dumps(counts) if counts else None,
            json.dumps(files) if files else None,
        ),
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--max-age-days", type=int, default=30,
                   help="Skip jsonls older than this (default 30)")
    args = p.parse_args()

    project_dir = _project_dir()
    if not project_dir.is_dir():
        print(f"No project dir at {project_dir}")
        return 1
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=args.max_age_days)

    # Snapshot the file list so progress reporting can show N/total.
    candidates: list[Path] = []
    for f in project_dir.glob("*.jsonl"):
        try:
            mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime, tz=datetime.timezone.utc)
        except OSError:
            continue
        if mtime >= cutoff:
            candidates.append(f)
    total = len(candidates)
    print(f"Scanning {total} jsonl files (modified within {args.max_age_days} days)…")

    start = time.time()
    processed = 0
    sub_processed = 0
    with _db.connection() as conn:
        cur = conn.cursor()
        for i, f in enumerate(candidates, 1):
            sid = f.stem
            try:
                session_id = _ensure_row(cur, sid, f)
                signals = hook_writer._extract_signals(sid)
                last_dt = datetime.datetime.fromtimestamp(
                    f.stat().st_mtime, tz=datetime.timezone.utc
                )
                _apply_signals_with_cursor(cur, session_id, signals, last_dt)
                processed += 1
                # Subagents.
                sub_dir = project_dir / sid / "subagents"
                this_subs = 0
                if sub_dir.is_dir():
                    for sf in sub_dir.glob("agent-*.jsonl"):
                        try:
                            _upsert_subagent_with_cursor(cur, sid, sf)
                            sub_processed += 1
                            this_subs += 1
                        except Exception as exc:  # noqa: BLE001
                            print(f"    FAIL subagent {sf.stem}: {exc}")
                conn.commit()
                title = signals.get("custom_title") or "(no name)"
                elapsed = time.time() - start
                rate = i / elapsed if elapsed > 0 else 0
                eta = (total - i) / rate if rate > 0 else 0
                print(f"  [{i}/{total}] {sid[:8]} ({title[:30]:30s}) +{this_subs} subs  eta={eta:.0f}s")
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i}/{total}] FAIL {sid[:8]}: {exc}")
        cur.close()
    elapsed = time.time() - start
    print(f"\nBackfill complete in {elapsed:.1f}s. sessions={processed} subagents={sub_processed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
