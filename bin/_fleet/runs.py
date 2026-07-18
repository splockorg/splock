"""Per-slug runs ledger — the C&C bookkeeping behind `fleet spawn/board/resume`.

`docs/plans/<slug>/_fleet_runs.jsonl` records every headless child
spawned for that slug, under the SAME write discipline as
`_fleet_log.jsonl`: per-slug path (no shared write target), append-only
single `O_APPEND` writes, lines clamped < PIPE_BUF, torn lines skipped
by every reader.

Row shapes (`event` discriminates):

    {"ts", "run_id", "slug", "stage", "event": "spawned"|"resumed",
     "pid", "model", "effort", "permission_mode",
     "session_id"}                      # session_id on resume rows only

    {"ts", "run_id", "slug", "stage", "event": "completed"|"failed",
     "exit_code", "session_id", "total_cost_usd", "is_error",
     "subtype", "num_turns", "denials", "result_snippet"}

A spawn writes the start row from the PARENT (so the ledger reflects
the spawn the moment the command returns) and the completion row from
the detached runner; `run_id` joins the pair. Liveness = a start row
with no matching completed/failed row whose recorded pid (the runner's)
is still alive — a dead pid with no completion row renders as a died
run, never a crash.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from bin._fleet import paths

RUNS_NAME = "_fleet_runs.jsonl"

#: result_snippet clamp — keeps every ledger line comfortably < PIPE_BUF.
MAX_SNIPPET_CHARS = 400

_START_EVENTS = ("spawned", "resumed")
_END_EVENTS = ("completed", "failed")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def runs_path(slug: str) -> Path:
    return paths.slug_dir(slug) / RUNS_NAME


def append_run(slug: str, row: dict) -> None:
    d = paths.slug_dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    snippet = row.get("result_snippet")
    if snippet and len(snippet) > MAX_SNIPPET_CHARS:
        row = {**row, "result_snippet": snippet[: MAX_SNIPPET_CHARS - 1] + "…"}
    line = json.dumps(row, ensure_ascii=False)
    # Same append-atomicity argument as engine.append_event: single
    # O_APPEND write, line < PIPE_BUF, per-slug path.
    with open(d / RUNS_NAME, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_runs(slug: str) -> list[dict]:
    p = runs_path(slug)
    if not p.exists():
        return []
    rows: list[dict] = []
    with open(p, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError:
                pass  # torn line: skip, never crash the board
    return rows


def load_all_runs() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    import glob as _glob

    for p in _glob.glob(str(paths.plans_dir() / "*" / RUNS_NAME)):
        slug = os.path.basename(os.path.dirname(p))
        try:
            out[slug] = load_runs(slug)
        except OSError as e:
            print(f"warn: skipping {p}: {e}", file=sys.stderr)
    return out


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def split_runs(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """(live, died, ended) for one slug's rows.

    live  — start row, no end row, runner pid alive
    died  — start row, no end row, runner pid gone (runner crashed hard)
    ended — completed/failed rows
    """
    ended = [r for r in rows if r.get("event") in _END_EVENTS]
    ended_ids = {r.get("run_id") for r in ended}
    live: list[dict] = []
    died: list[dict] = []
    for r in rows:
        if r.get("event") not in _START_EVENTS or r.get("run_id") in ended_ids:
            continue
        (live if pid_alive(r.get("pid", -1)) else died).append(r)
    return live, died, ended


def live_run_count() -> int:
    """Live headless children across every slug (the concurrency-cap input)."""
    n = 0
    for rows in load_all_runs().values():
        live, _, _ = split_runs(rows)
        n += len(live)
    return n


def latest_session_id(slug: str) -> str | None:
    """Newest session handle for a slug: the last row carrying one."""
    for r in reversed(load_runs(slug)):
        sid = r.get("session_id")
        if sid:
            return sid
    return None
