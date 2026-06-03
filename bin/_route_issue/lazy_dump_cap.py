"""Per-session + per-machine cap accounting (implplan §L.impl.7).

Two caps from plan §L.6:

**Per-session hard cap**
  - counter: ~/.claude/logs/lazy-dump-counter-<session_id>.json
  - threshold: LAZY_DUMP_CAP (default 6; GUARDRAIL 3)
  - what counts: only --type outstanding appends
  - enforcement: pre-commit refuses with exit 26 if breached
  - override: none operator-visible; agent must downgrade or upgrade

**Per-machine soft cap**
  - counter: ~/.claude/logs/lazy-dump-counter-machine-rolling.json
  - threshold: 12 appends per rolling hour across all sessions
  - enforcement: WARN only (no refuse); morning-report row aggregates daily

`COUNTABLE_TYPES = {"outstanding"}` per plan §L.6 paragraph 3 + Hole H.24;
marker / tier-promote / escalate do NOT count.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DEFAULT_CAP = 6
GUARDRAIL_CAP = 3
ENV_CAP = "LAZY_DUMP_CAP"
ENV_GUARDRAIL = "OVERNIGHT_GUARDRAIL"  # when set to "1", enforce GUARDRAIL_CAP
MACHINE_SOFT_CAP_PER_HOUR = 12

COUNTABLE_TYPES = frozenset({"outstanding"})


def should_increment(route_type: str) -> bool:
    """True iff `route_type` is in the counted set (per Hole H.24)."""
    return route_type in COUNTABLE_TYPES


def cap_threshold(env: Optional[dict] = None) -> int:
    """Compute the hard cap from env. Guardrail overrides default."""
    env = env if env is not None else os.environ
    if env.get(ENV_GUARDRAIL) == "1":
        return GUARDRAIL_CAP
    val = env.get(ENV_CAP)
    if val is None:
        return DEFAULT_CAP
    try:
        n = int(val)
        if n < 0:
            return DEFAULT_CAP
        return n
    except ValueError:
        return DEFAULT_CAP


def _session_id(env: Optional[dict] = None) -> str:
    env = env if env is not None else os.environ
    return env.get("CLAUDE_SESSION_ID") or "sess_default"


def _counter_dir(home_override: Optional[Path] = None) -> Path:
    base = home_override or Path(os.path.expanduser("~"))
    return base / ".claude" / "logs"


def _session_counter_path(home_override: Optional[Path] = None,
                          env: Optional[dict] = None) -> Path:
    sid = _session_id(env)
    return _counter_dir(home_override) / f"lazy-dump-counter-{sid}.json"


def _machine_counter_path(home_override: Optional[Path] = None) -> Path:
    return _counter_dir(home_override) / "lazy-dump-counter-machine-rolling.json"


def _read_session(path: Path) -> dict:
    if not path.exists():
        return {"count": 0, "last_ts": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"count": 0, "last_ts": None}


def _write_session(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def session_count(home_override: Optional[Path] = None,
                  env: Optional[dict] = None) -> int:
    """Return the current session counter (0 if no file)."""
    path = _session_counter_path(home_override, env)
    return int(_read_session(path).get("count", 0))


def increment_session(home_override: Optional[Path] = None,
                      env: Optional[dict] = None) -> int:
    """Increment the per-session counter; return new value."""
    path = _session_counter_path(home_override, env)
    data = _read_session(path)
    data["count"] = int(data.get("count", 0)) + 1
    data["last_ts"] = _now_iso()
    _write_session(path, data)
    return data["count"]


def reset_session(home_override: Optional[Path] = None,
                  env: Optional[dict] = None) -> None:
    """Zero the per-session counter. Operator-only path (logged via §C)."""
    path = _session_counter_path(home_override, env)
    _write_session(path, {"count": 0, "last_ts": _now_iso()})


def record_machine_append(home_override: Optional[Path] = None) -> int:
    """Append a timestamp to the rolling machine log. Returns count within last hour.

    The log is a JSONL-like list of UNIX timestamps; entries older than
    1 hour are dropped on each call (rolling window).
    """
    path = _machine_counter_path(home_override)
    path.parent.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 3600.0
    items: list = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                raw = data.get("timestamps", [])
                if isinstance(raw, list):
                    items = [float(t) for t in raw if isinstance(t, (int, float))]
        except (json.JSONDecodeError, OSError, ValueError):
            items = []
    items = [t for t in items if t >= cutoff]
    items.append(time.time())
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"timestamps": items}, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return len(items)


def machine_count(home_override: Optional[Path] = None) -> int:
    """Return the rolling-hour count without writing a new entry."""
    path = _machine_counter_path(home_override)
    if not path.exists():
        return 0
    cutoff = time.time() - 3600.0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(data, dict):
        return 0
    raw = data.get("timestamps", [])
    if not isinstance(raw, list):
        return 0
    items = [float(t) for t in raw if isinstance(t, (int, float))]
    return sum(1 for t in items if t >= cutoff)
