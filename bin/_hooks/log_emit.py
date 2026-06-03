"""`bin/hook-log` core implementation.

Per implplan §G.impl.11. Appends one JSON line to
`~/.claude/logs/hooks-<YYYY-MM-DD>.jsonl` (per-user; per-day rotation).

Row shape:

    {"ts": "<ISO>", "hook": "<name>", "action": "<verb>",
     "message": "<msg>", "session_id": "<$CLAUDE_SESSION_ID>",
     "slug": "<$SPLOCK_PLAN_SLUG>", "chain_id": "<$SPLOCK_CHAIN_ID>",
     "phase": "<$SPLOCK_PHASE>", "pid": <getpid>}

Action enum: {"ok", "blocked", "flagged", "error"}.

Implementation seam (per §G.impl.11): `bin/log` and `bin/hook-log` share
this writer parameterized on `mode` + `target_root_env`.
"""

from __future__ import annotations

import datetime
import fcntl
import json
import os
from dataclasses import dataclass
from pathlib import Path

from bin._hooks import HOOK_LOG_ACTIONS


HOOK_LOG_ROOT_ENV: str = "HOOK_LOG_ROOT"
CLI_LOG_ROOT_ENV: str = "CLI_LOG_ROOT"

DEFAULT_HOOK_LOG_ROOT: Path = Path.home() / ".claude" / "logs"
DEFAULT_CLI_LOG_ROOT: Path = Path.home() / ".claude" / "logs"

MAX_MESSAGE_LEN: int = 256


@dataclass(frozen=True)
class EmitResult:
    """Return value from `emit(...)`.

    `wrote_path` is the JSONL file actually appended to (informational).
    `accepted` is True iff the row passed validation.
    `reason` carries the rejection reason on `accepted=False`.
    """
    accepted: bool
    wrote_path: Path | None
    reason: str | None = None


def _today_utc_yyyy_mm_dd() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _now_iso_z() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _resolve_root(mode: str) -> Path:
    if mode == "hook":
        override = os.environ.get(HOOK_LOG_ROOT_ENV, "").strip()
        return Path(override) if override else DEFAULT_HOOK_LOG_ROOT
    if mode == "cli":
        override = os.environ.get(CLI_LOG_ROOT_ENV, "").strip()
        return Path(override) if override else DEFAULT_CLI_LOG_ROOT
    raise ValueError(f"mode must be 'hook' or 'cli'; got {mode!r}")


def _resolve_target_file(mode: str) -> Path:
    root = _resolve_root(mode)
    root.mkdir(parents=True, exist_ok=True)
    prefix = "hooks" if mode == "hook" else "cli"
    return root / f"{prefix}-{_today_utc_yyyy_mm_dd()}.jsonl"


def _read_env_context() -> dict:
    return {
        "session_id": os.environ.get("CLAUDE_SESSION_ID", ""),
        "slug": os.environ.get("SPLOCK_PLAN_SLUG", ""),
        "chain_id": os.environ.get("SPLOCK_CHAIN_ID", ""),
        "phase": os.environ.get("SPLOCK_PHASE", ""),
    }


def emit(
    mode: str,
    name: str,
    action: str,
    message: str,
    known_writers: frozenset[str] | None = None,
) -> EmitResult:
    """Append a structured row to the appropriate JSONL.

    Parameters
    ----------
    mode : "hook" | "cli"
        "hook" → `~/.claude/logs/hooks-<date>.jsonl`, key `hook`.
        "cli"  → `~/.claude/logs/cli-<date>.jsonl`, key `emitter`.
    name : str
        Hook name (kebab-case) or KNOWN_WRITERS emitter value.
    action : str
        Must be in HOOK_LOG_ACTIONS.
    message : str
        ≤ MAX_MESSAGE_LEN; truncated if longer.
    known_writers : frozenset[str] | None
        When mode == "cli", `name` must be in this set (else rejected).
        For hook mode this is informational; we don't enforce a
        hook-name allowlist here (hook-lint owns kebab-case rule).

    Concurrent emissions from multiple PIDs serialize via fcntl flock
    on the target file (advisory; same FS).
    """
    if action not in HOOK_LOG_ACTIONS:
        return EmitResult(
            accepted=False,
            wrote_path=None,
            reason=f"action={action!r} not in {sorted(HOOK_LOG_ACTIONS)}",
        )
    if not name or not isinstance(name, str):
        return EmitResult(
            accepted=False, wrote_path=None,
            reason="name must be non-empty string",
        )
    if mode == "cli" and known_writers is not None and name not in known_writers:
        return EmitResult(
            accepted=False, wrote_path=None,
            reason=f"emitter={name!r} not in KNOWN_WRITERS",
        )
    # Truncate over-long message.
    msg = (message or "")[:MAX_MESSAGE_LEN]
    ctx = _read_env_context()
    name_key = "hook" if mode == "hook" else "emitter"
    row = {
        "ts": _now_iso_z(),
        name_key: name,
        "action": action,
        "message": msg,
        "session_id": ctx["session_id"],
        "slug": ctx["slug"],
        "chain_id": ctx["chain_id"],
        "phase": ctx["phase"],
        "pid": os.getpid(),
    }
    line = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
    target = _resolve_target_file(mode)
    # Append with advisory exclusive lock for cross-PID serialization.
    with target.open("a", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            fh.write(line)
            fh.flush()
        finally:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    return EmitResult(accepted=True, wrote_path=target)


__all__ = [
    "EmitResult",
    "emit",
    "MAX_MESSAGE_LEN",
    "HOOK_LOG_ROOT_ENV",
    "CLI_LOG_ROOT_ENV",
]
