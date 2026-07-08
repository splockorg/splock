"""Local-JSONL append + reconciliation for the intent mirror `intent_local.jsonl`.

Per implplan §P.impl.8 hybrid storage. Local-JSONL write is SYNCHRONOUS
+ REQUIRED (registration fails if local write fails — exit 7). MySQL
write-through is best-effort; failure flags `sync_pending=true` and
`bin/intent doctor` reconciles.

Atomic temp+rename under `flock` on `<file>.lock` per cross-cutting
implplan lines 281-290. Path is sealed per §P.impl.8 + §G.impl.5
extension — co-located under the plugin data-root's `.plugin-data/` and sealed
via the `.plugin-data/**` glob in `sealed_paths.txt`.

JSONL row schema (forward-compat permissive read):
  - Pre-T1 rows lack `claude_session_id` — readers MUST tolerate
    missing key (treat as None / NULL).
  - T1 rows (intent_session_auto_register) add
    `claude_session_id: <str|None>` — populated when register receives
    `--claude-session-id <id>` (interactive SessionStart auto-register
    per T3); NULL for chain-overnight rows + pre-T1 rows.
"""

from __future__ import annotations

import fcntl
import json
import os
import pathlib
import tempfile
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from . import db

JSONL_FILE_NAME = "intent_local.jsonl"


def intent_jsonl_path(repo_root: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Resolve the local-JSONL mirror path.

    Routed through :func:`bin._intent.db.resolve_data_root` so the mirror
    co-locates with the SQLite db + settings overlay under the plugin
    data-root (``$CLAUDE_PLUGIN_DATA`` -> ``$CLAUDE_PROJECT_DIR/.plugin-data``
    -> ``./.plugin-data``), NEVER under the ``parents[2]`` repo root. The old
    ``docs/intent`` layout leaked mutable state into the adopter repo and sat
    in the ephemeral cache dir wiped ~7d post plugin-update (SC-C #4/#5). The
    optional ``repo_root`` arg is an explicit data-root override for tests,
    matching ``db.intent_sqlite_path``'s ``data_root`` semantics.
    """
    return db.resolve_data_root(repo_root) / JSONL_FILE_NAME


def intent_jsonl_lock_path(repo_root: Optional[pathlib.Path] = None) -> pathlib.Path:
    return intent_jsonl_path(repo_root).with_suffix(".jsonl.lock")


@contextmanager
def _acquire_flock(lock_path: pathlib.Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def append_row(
    row: dict,
    *,
    repo_root: Optional[pathlib.Path] = None,
) -> pathlib.Path:
    """Atomic temp+rename append under flock.

    Per cross-cutting atomic-write discipline. Single newline-terminated
    JSON line. Returns the target path.
    """
    target = intent_jsonl_path(repo_root)
    lock = intent_jsonl_lock_path(repo_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n"

    with _acquire_flock(lock):
        # Read existing bytes to preserve order on temp+rename.
        existing = target.read_bytes() if target.exists() else b""
        new_bytes = existing + payload.encode("utf-8")
        # Atomic write-temp + rename under the SAME directory.
        with tempfile.NamedTemporaryFile(
            dir=str(target.parent),
            prefix=".intent_local.",
            suffix=".tmp",
            delete=False,
        ) as fh:
            fh.write(new_bytes)
            fh.flush()
            os.fsync(fh.fileno())
            tmp_name = fh.name
        os.replace(tmp_name, str(target))
    return target


def read_all(repo_root: Optional[pathlib.Path] = None) -> list[dict[str, Any]]:
    """Return all rows. Skips malformed lines silently (doctor surfaces them)."""
    target = intent_jsonl_path(repo_root)
    if not target.exists():
        return []
    out: list[dict] = []
    with target.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def rewrite_all(
    rows: list[dict],
    *,
    repo_root: Optional[pathlib.Path] = None,
) -> pathlib.Path:
    """Atomically rewrite the entire JSONL with the given row sequence.

    Used by `doctor` to mutate rows in place (e.g., clear sync_pending,
    mark collision_blocked). Always under flock + atomic temp+rename.
    """
    target = intent_jsonl_path(repo_root)
    lock = intent_jsonl_lock_path(repo_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows
    )
    with _acquire_flock(lock):
        with tempfile.NamedTemporaryFile(
            dir=str(target.parent),
            prefix=".intent_local.",
            suffix=".tmp",
            delete=False,
        ) as fh:
            fh.write(payload.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
            tmp_name = fh.name
        os.replace(tmp_name, str(target))
    return target


def find_active_for_host(
    host: str,
    *,
    repo_root: Optional[pathlib.Path] = None,
) -> list[dict]:
    """Return rows matching `host` AND not closed_at. Used by the hook
    resolver (PreToolUse) — local-JSONL-only read for hot-path latency
    per P.impl.9 step 2."""
    return [
        r for r in read_all(repo_root=repo_root)
        if r.get("host") == host and not r.get("closed_at")
    ]


def find_by_session_id(
    session_id: str,
    *,
    repo_root: Optional[pathlib.Path] = None,
) -> Optional[dict]:
    for r in read_all(repo_root=repo_root):
        if r.get("session_id") == session_id:
            return r
    return None


def find_by_claude_session_id(
    claude_session_id: str,
    *,
    repo_root: Optional[pathlib.Path] = None,
) -> list[dict]:
    """T1 (intent_session_auto_register): return rows whose
    `claude_session_id` matches. Used by `bin/intent list --claude-session
    <id>` for the local-JSONL fallback when MySQL is unreachable. Pre-T1
    rows without the key are skipped."""
    return [
        r for r in read_all(repo_root=repo_root)
        if r.get("claude_session_id") == claude_session_id
    ]


__all__ = [
    "intent_jsonl_path",
    "intent_jsonl_lock_path",
    "append_row",
    "read_all",
    "rewrite_all",
    "find_active_for_host",
    "find_by_session_id",
    "find_by_claude_session_id",
]
