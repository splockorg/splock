"""Phase B writer for intent_session_auto_register — populates the local-
overlay signals into RDS columns on `agent_sessions` so the console works
cross-machine.

Two entry points called by hook shell scripts:

  python -m bin._intent.hook_writer user_prompt --session-id <uuid>
  python -m bin._intent.hook_writer stop --session-id <uuid>

Each scans the relevant Claude session jsonl(s) and upserts the row.

Designed to be fast + fail-open: any error logs to hook-log and exits 0
(matching the SessionStart / UserPromptSubmit contract).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import secrets
import socket
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Hooks invoke this module via subprocess from Claude Code, whose process
# env has no SPLOCK_DB_* vars. Without this, every DB write silently no-ops
# via _db.MySQLUnavailable and the session-tracking columns never populate.
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from . import db as _db  # noqa: E402

# Caps so JSON columns don't unbounded-grow on long sessions.
RECENT_PROMPTS_MAX = 5
RECENT_PROMPT_CHARS = 240
FILES_TOUCHED_MAX = 40
JSONL_TAIL_BYTES = 1 * 1024 * 1024  # last 1MB of jsonl is usually enough

_WORKFLOW_SKILLS = frozenset({
    "recon", "research", "qa", "qna", "plan", "implplan",
    "code", "test", "review", "loop",
})

_CMD_NAME_RE = re.compile(rb"<command-name>/([a-z_]{1,30})</command-name>")
_GIT_BRANCH_RE = re.compile(rb'"gitBranch":"([^"]{1,200})"')
_CUSTOM_TITLE_RE = re.compile(rb'"customTitle":"([^"]{1,200})"')
_TOOL_USE_RE = re.compile(rb'"type":"tool_use","id":"toolu_[^"]+","name":"([A-Za-z_][A-Za-z0-9_]{0,40})"')
_FILE_PATH_RE = re.compile(rb'"file_path":"([^"]{1,300})"')
# Match ONLY top-level user-message rows (not assistant-content-embedded
# quotes of "type":"user"). Heuristic: look for the prompt content marker.
_USER_PROMPT_LINE_HINT = b'"type":"user"'


def _claude_project_dir() -> Path:
    cwd = os.path.abspath(os.getcwd())
    munged = "-" + cwd.lstrip("/").replace("/", "-")
    return Path.home() / ".claude" / "projects" / munged


def _jsonl_path(session_id: str) -> Path:
    return _claude_project_dir() / f"{session_id}.jsonl"


def _read_tail(path: Path, max_bytes: int = JSONL_TAIL_BYTES) -> bytes:
    try:
        size = path.stat().st_size
    except OSError:
        return b""
    if size == 0:
        return b""
    chunk = min(size, max_bytes)
    try:
        with path.open("rb") as fh:
            fh.seek(size - chunk)
            return fh.read(chunk)
    except OSError:
        return b""


def _last_match(pat: re.Pattern, data: bytes) -> Optional[str]:
    last = None
    for m in pat.finditer(data):
        last = m.group(1)
    if last is None:
        return None
    return last.decode("utf-8", errors="replace")


def _extract_recent_prompts(data: bytes) -> list[str]:
    """Pull last N user prompts. Each jsonl line is a JSON object; lines
    with `"type":"user"` carry the prompt content under `message.content`.
    """
    prompts: list[str] = []
    for line in reversed(data.splitlines()):
        if _USER_PROMPT_LINE_HINT not in line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue
        if obj.get("type") != "user":
            continue
        # Skip slash-command / meta wrappers.
        if obj.get("isMeta"):
            continue
        msg = obj.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            for piece in content:
                if isinstance(piece, dict) and piece.get("type") == "text":
                    content = piece.get("text", "")
                    break
            else:
                content = ""
        if not isinstance(content, str) or not content.strip():
            continue
        # Skip system-injected wrappers.
        if content.startswith("<command-name>") or content.startswith("<local-command"):
            continue
        if content.startswith("<system-reminder>"):
            continue
        prompts.append(content[:RECENT_PROMPT_CHARS])
        if len(prompts) >= RECENT_PROMPTS_MAX:
            break
    prompts.reverse()
    return prompts


def _extract_tools_used_count(data: bytes) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in _TOOL_USE_RE.finditer(data):
        name = m.group(1).decode("ascii", errors="replace")
        counts[name] = counts.get(name, 0) + 1
    return counts


def _extract_files_touched(data: bytes) -> list[dict]:
    """Aggregate file_path occurrences (from Edit/Write/Read tool_input).
    We deliberately count ALL file_path matches because the same file
    edited 3 times should appear with edits=3. Capped to FILES_TOUCHED_MAX
    by edit count desc.
    """
    counts: dict[str, int] = {}
    for m in _FILE_PATH_RE.finditer(data):
        path = m.group(1).decode("utf-8", errors="replace")
        counts[path] = counts.get(path, 0) + 1
    items = [{"path": p, "edits": n} for p, n in counts.items()]
    items.sort(key=lambda x: x["edits"], reverse=True)
    return items[:FILES_TOUCHED_MAX]


def _extract_todo_state(data: bytes) -> Optional[list]:
    """Reconstruct the operator's task list from TaskCreate/TaskUpdate
    tool_use entries.

    TaskCreate emits one task at a time (subject, description). TaskUpdate
    mutates an existing task by ID. We replay all TaskCreate/TaskUpdate
    calls in order to build the live task table, then return it as a list
    of {id, subject, status} dicts. Capped to avoid bloat on very long
    sessions.

    Falls back to None if no Task* calls present (legacy TodoWrite path).
    """
    tasks: dict[str, dict] = {}
    next_id = 1
    for line in data.splitlines():
        if b'"name":"TaskCreate"' not in line and b'"name":"TaskUpdate"' not in line and b'"name":"TodoWrite"' not in line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue
        msg = obj.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for piece in content:
            if not isinstance(piece, dict) or piece.get("type") != "tool_use":
                continue
            name = piece.get("name")
            inp = piece.get("input") or {}
            if name == "TaskCreate":
                tid = str(next_id)
                next_id += 1
                tasks[tid] = {
                    "id": tid,
                    "subject": (inp.get("subject") or "")[:120],
                    "status": "pending",
                }
            elif name == "TaskUpdate":
                tid = str(inp.get("taskId") or inp.get("task_id") or "")
                if tid in tasks:
                    st = inp.get("status")
                    if st:
                        tasks[tid]["status"] = st
                    sub = inp.get("subject")
                    if sub:
                        tasks[tid]["subject"] = sub[:120]
            elif name == "TodoWrite":
                todos = inp.get("todos")
                if isinstance(todos, list):
                    tasks = {str(i): {"id": str(i), "subject": (t.get("content") or t.get("subject") or "")[:120],
                                       "status": t.get("status") or "pending"}
                              for i, t in enumerate(todos)}
                    next_id = len(todos) + 1
    if not tasks:
        return None
    return list(tasks.values())[-30:]


def _extract_workflow_stage(data: bytes) -> Optional[str]:
    matches = _CMD_NAME_RE.findall(data)
    for raw in reversed(matches):
        skill = raw.decode("ascii", errors="replace")
        if skill in _WORKFLOW_SKILLS:
            return skill
    return None


def _extract_signals(session_id: str) -> dict:
    """Read the session jsonl and return all extractable signals."""
    path = _jsonl_path(session_id)
    out: dict = {
        "custom_title": None,
        "git_branch": None,
        "workflow_stage": None,
        "recent_prompts": None,
        "todo_state": None,
        "tools_used_count": None,
        "files_touched": None,
    }
    if not path.is_file():
        return out
    data = _read_tail(path)
    if not data:
        return out
    out["custom_title"] = _last_match(_CUSTOM_TITLE_RE, data)
    out["git_branch"] = _last_match(_GIT_BRANCH_RE, data)
    out["workflow_stage"] = _extract_workflow_stage(data)
    prompts = _extract_recent_prompts(data)
    if prompts:
        out["recent_prompts"] = prompts
    counts = _extract_tools_used_count(data)
    if counts:
        out["tools_used_count"] = counts
    files = _extract_files_touched(data)
    if files:
        out["files_touched"] = files
    todos = _extract_todo_state(data)
    if todos is not None:
        out["todo_state"] = todos
    return out


def _live_status_for(session_id: str) -> Optional[str]:
    """Read ~/.claude/sessions/*.json for the given sessionId and return
    its `status` (idle/busy). Returns None when no PID file matches the id.
    """
    sessions_dir = Path.home() / ".claude" / "sessions"
    if not sessions_dir.is_dir():
        return None
    best = None
    best_updated = -1
    for f in sessions_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("sessionId") != session_id:
            continue
        u = data.get("updatedAt", 0)
        if u > best_updated:
            best_updated = u
            best = data
    if best is None:
        return None
    return best.get("status")


def _ensure_row_exists(cur, claude_session_id: str) -> str:
    """Find an open agent_sessions row for this claude_session_id, or
    INSERT a fresh one. Returns the session_id (PK) we should UPDATE.
    """
    cur.execute(
        "SELECT session_id FROM extraction.agent_sessions "
        "WHERE claude_session_id = %s AND closed_at IS NULL "
        "ORDER BY started_at DESC LIMIT 1",
        (claude_session_id,),
    )
    row = cur.fetchone()
    if row:
        return row[0] if isinstance(row, tuple) else row["session_id"]
    # No open row — fresh insert with defaults matching the SessionStart hook.
    sid = f"sess_{datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}_{secrets.token_hex(2)}"
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
           %s, UTC_TIMESTAMP(), UTC_TIMESTAMP(), NULL,
           %s, %s)
        """,
        (sid, host, "user_prompt_submit_auto", claude_session_id),
    )
    return sid


def _apply_signals(claude_session_id: str, signals: dict, *, live_status: Optional[str],
                   bump_user_prompt: bool = False, bump_assistant: bool = False) -> None:
    """Upsert the Phase B columns for `claude_session_id`. Best-effort."""
    try:
        with _db.connection() as conn:
            cur = conn.cursor()
            session_id = _ensure_row_exists(cur, claude_session_id)
            sets: list[str] = []
            params: list = []
            for col in ("custom_title", "git_branch", "workflow_stage"):
                v = signals.get(col)
                if v is not None:
                    sets.append(f"{col} = %s")
                    params.append(v)
            for col in ("recent_prompts", "todo_state", "tools_used_count", "files_touched"):
                v = signals.get(col)
                if v is not None:
                    sets.append(f"{col} = %s")
                    params.append(json.dumps(v))
            if live_status is not None:
                sets.append("live_status = %s")
                params.append(live_status)
            if bump_user_prompt:
                sets.append("last_user_prompt_at = UTC_TIMESTAMP()")
            if bump_assistant:
                sets.append("last_assistant_at = UTC_TIMESTAMP()")
            # Always bump activity at hook fire time so the "Last activity"
            # column reflects real interaction even without per-turn writes.
            sets.append("last_activity_at = UTC_TIMESTAMP()")
            if not sets:
                return
            params.append(session_id)
            cur.execute(
                "UPDATE extraction.agent_sessions SET " + ", ".join(sets) +
                " WHERE session_id = %s",
                tuple(params),
            )
            conn.commit()
    except _db.MySQLUnavailable:
        # No fallback — Phase B is RDS-first; the index view's discovery
        # overlay still surfaces the row from local files.
        return


def _hook_log(action: str, message: str) -> None:
    import subprocess
    repo_root = Path(__file__).resolve().parent.parent.parent
    binpath = repo_root / "bin" / "hook-log"
    if not binpath.exists():
        return
    try:
        subprocess.run(
            [str(binpath), "intent-hook-writer", action, message[:200]],
            timeout=3, check=False, capture_output=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _find_recent_subagent_file(parent_session_id: str) -> Optional[Path]:
    """Return the most-recently-modified agent-*.jsonl under the parent's
    subagents dir, or None if no such dir/file exists.

    The SubagentStop hook's envelope doesn't always carry the subagent_id
    directly; the convention is that the most-recently-touched file is
    the one whose Stop just fired.
    """
    sub_dir = _claude_project_dir() / parent_session_id / "subagents"
    if not sub_dir.is_dir():
        return None
    candidates = list(sub_dir.glob("agent-*.jsonl"))
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.stat().st_mtime if f.exists() else 0)


def _upsert_subagent(parent_session_id: str, jsonl_path: Path) -> Optional[str]:
    """UPSERT one row into agent_subagents from the given jsonl file +
    its sibling .meta.json. Returns the subagent_id on success, None on
    failure.
    """
    subagent_id = jsonl_path.stem  # "agent-aa02c20d672162508"
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
        return None
    last_activity = datetime.datetime.fromtimestamp(stat.st_mtime, tz=datetime.timezone.utc)

    # Tools + files scoped to this subagent's jsonl only.
    data = _read_tail(jsonl_path)
    counts = _extract_tools_used_count(data) if data else {}
    files = _extract_files_touched(data) if data else []

    try:
        with _db.connection() as conn:
            cur = conn.cursor()
            # Use INSERT ... ON DUPLICATE KEY UPDATE to upsert by PK.
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
                    status        = VALUES(status),
                    tools_used_count = VALUES(tools_used_count),
                    files_touched = VALUES(files_touched)
                """,
                (
                    subagent_id,
                    parent_session_id,
                    agent_type or None,
                    description or None,
                    last_activity,  # started_at — best approximation
                    last_activity,
                    json.dumps(counts) if counts else None,
                    json.dumps(files) if files else None,
                ),
            )
            conn.commit()
            return subagent_id
    except _db.MySQLUnavailable:
        return None


def _cmd_subagent_stop(parent_session_id: str) -> int:
    jsonl = _find_recent_subagent_file(parent_session_id)
    if jsonl is None:
        _hook_log("ok", f"subagent_stop sid={parent_session_id[:8]} no_subagent_file")
        return 0
    sid = _upsert_subagent(parent_session_id, jsonl)
    _hook_log(
        "ok" if sid else "error",
        f"subagent_stop parent={parent_session_id[:8]} subagent={(sid or 'none')[:18]}",
    )
    return 0


_VALID_CLOSE_REASONS = frozenset({"logout", "clear", "prompt_input_exit", "other"})


def _cmd_session_end(claude_session_id: str, reason: Optional[str] = None) -> int:
    """Mark the parent session row closed: closed_at=NOW, live_status='closed'.
    Also marks any still-open subagent rows for this parent as 'done'.

    `reason` is the SessionEnd payload's `reason` field. Unknown values
    fall through as 'other' so the column stays in a small known enum.
    """
    if reason is not None:
        reason = reason.strip().lower()
        if reason not in _VALID_CLOSE_REASONS:
            reason = "other" if reason else None
    try:
        with _db.connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE extraction.agent_sessions
                   SET closed_at = UTC_TIMESTAMP(),
                       live_status = 'closed',
                       last_activity_at = UTC_TIMESTAMP(),
                       close_reason = COALESCE(%s, close_reason)
                 WHERE claude_session_id = %s AND closed_at IS NULL
                """,
                (reason, claude_session_id),
            )
            n_sessions = cur.rowcount
            cur.execute(
                """
                UPDATE extraction.agent_subagents
                   SET status = 'done', closed_at = UTC_TIMESTAMP()
                 WHERE parent_claude_session_id = %s
                   AND (status IS NULL OR status != 'done')
                """,
                (claude_session_id,),
            )
            n_subagents = cur.rowcount
            conn.commit()
            _hook_log(
                "ok",
                f"session_end sid={claude_session_id[:8]} reason={reason or '-'} "
                f"closed_sessions={n_sessions} closed_subagents={n_subagents}",
            )
    except _db.MySQLUnavailable:
        _hook_log("error", f"session_end sid={claude_session_id[:8]} mysql_unavailable")
    return 0


def _cmd_user_prompt(claude_session_id: str) -> int:
    signals = _extract_signals(claude_session_id)
    live = _live_status_for(claude_session_id) or "busy"
    _apply_signals(claude_session_id, signals, live_status=live, bump_user_prompt=True)
    _hook_log("ok", f"user_prompt sid={claude_session_id[:8]} stage={signals.get('workflow_stage')}")
    return 0


def _cmd_stop(claude_session_id: str) -> int:
    signals = _extract_signals(claude_session_id)
    live = _live_status_for(claude_session_id) or "idle"
    _apply_signals(claude_session_id, signals, live_status=live, bump_assistant=True)
    _hook_log("ok", f"stop sid={claude_session_id[:8]} tools={sum((signals.get('tools_used_count') or {}).values())}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bin._intent.hook_writer")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("user_prompt", "stop", "subagent_stop", "session_end"):
        sp = sub.add_parser(name)
        sp.add_argument("--session-id", required=True, dest="session_id")
        if name == "session_end":
            sp.add_argument("--reason", required=False, dest="reason", default=None)
    args = p.parse_args(argv)
    sid = (args.session_id or "").strip()
    if not sid:
        return 0
    try:
        if args.cmd == "user_prompt":
            return _cmd_user_prompt(sid)
        if args.cmd == "stop":
            return _cmd_stop(sid)
        if args.cmd == "subagent_stop":
            return _cmd_subagent_stop(sid)
        if args.cmd == "session_end":
            return _cmd_session_end(sid, reason=getattr(args, "reason", None))
    except Exception as exc:  # noqa: BLE001
        _hook_log("error", f"{args.cmd}: {exc}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
