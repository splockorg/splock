"""`--type outstanding` handler (implplan §L.impl).

Appends one entry to `docs/outstanding_issues.md` with the L.impl.6 line
shape. Mints a fresh `line_id`. Lazy-dump-cap is consulted BEFORE the
append (cap-hit refused with exit 26).

Atomic write discipline (per cross-cutting lines 281-285): tempfile in
the same directory + os.replace + flock over the full read-modify-write
cycle on `outstanding_issues.md.lock`.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import lazy_dump_cap, line_format, log_emit
from .exit_codes import (
    EXIT_ATOMIC_WRITE_FAILED,
    EXIT_OK,
    EXIT_OUTSTANDING_CAP_EXCEEDED,
)


OUTSTANDING_REL = "docs/outstanding_issues.md"
LOCK_SUFFIX = ".lock"


def _outstanding_path(repo_root: Path) -> Path:
    return repo_root / OUTSTANDING_REL


def _flock_outstanding(path: Path):
    """Acquire LOCK_EX on `<path>.lock`. Caller uses as context manager."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        lockfile = Path(str(path) + LOCK_SUFFIX)
        lockfile.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lockfile), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    return _ctx()


def _atomic_append(path: Path, text_block: str) -> None:
    """Append `text_block` to `path` atomically.

    Read current content, append new block (with leading newline if
    file is non-empty + doesn't end in newline), write via tempfile +
    os.replace.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    if current and not current.endswith("\n"):
        current += "\n"
    new_text = current + text_block + "\n"
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def run(
    *,
    description: str,
    context: str,
    blast_radius: Optional[int] = None,
    related: Optional[str] = None,
    dry_run: bool = False,
    json_output: bool = False,
    repo_root: Path,
    plan_slug: Optional[str] = None,
    task_id: str = "",
    emitted_by: str = "bin/route_issue:outstanding",
    home_override: Optional[Path] = None,
    env: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> int:
    """Append one outstanding-issues entry.

    Returns:
      EXIT_OK on success / dry-run.
      EXIT_OUTSTANDING_CAP_EXCEEDED if hard cap breached pre-append.
      EXIT_ATOMIC_WRITE_FAILED on filesystem failure.
    """
    line_format.validate_emitted_by(emitted_by)

    # Cap check FIRST (cheap, refuse before any work)
    threshold = lazy_dump_cap.cap_threshold(env)
    current = lazy_dump_cap.session_count(home_override, env)
    if current >= threshold:
        msg = (
            f"outstanding_cap_exceeded: session count={current} "
            f"meets/exceeds cap={threshold}; downgrade some entries to "
            f"fix-now or upgrade to escalate / tier-promote"
        )
        if json_output:
            print(json.dumps({
                "error": "outstanding_cap_exceeded",
                "session_count": current,
                "cap": threshold,
                "message": msg,
            }))
        else:
            print(msg, file=sys.stderr)
        return EXIT_OUTSTANDING_CAP_EXCEEDED

    when = now or datetime.now(timezone.utc)
    timestamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    line_id = line_format.mint_line_id(when)

    entry = line_format.OutstandingEntry(
        timestamp=timestamp,
        plan_slug=plan_slug or "splock",
        task_id=task_id or "",
        emitted_by=emitted_by,
        gloss=description,
        context=context,
        blast_radius=blast_radius,
        related=related,
        line_id=line_id,
        status="open",
    )
    rendered = line_format.render_entry(entry)

    if dry_run:
        if json_output:
            print(json.dumps({
                "result": "dry-run",
                "type": "outstanding",
                "line_id": line_id,
                "rendered": rendered,
            }))
        else:
            print("[dry-run] would append:")
            print(rendered)
        return EXIT_OK

    target = _outstanding_path(repo_root)
    try:
        with _flock_outstanding(target):
            _atomic_append(target, rendered)
    except OSError as e:
        msg = f"atomic_write_failed: {e}"
        if json_output:
            print(json.dumps({"error": "atomic_write_failed", "message": str(e)}))
        else:
            print(msg, file=sys.stderr)
        return EXIT_ATOMIC_WRITE_FAILED

    # Increment session counter + record machine append (best-effort)
    new_count = lazy_dump_cap.increment_session(home_override, env)
    machine_count = lazy_dump_cap.record_machine_append(home_override)
    if machine_count > lazy_dump_cap.MACHINE_SOFT_CAP_PER_HOUR:
        print(
            f"WARN: per-machine lazy-dump soft cap exceeded "
            f"({machine_count}/{lazy_dump_cap.MACHINE_SOFT_CAP_PER_HOUR} in last hour)",
            file=sys.stderr,
        )

    # Emit log row
    plan_dir = log_emit.resolve_plan_dir(None, plan_slug)
    log_emit.emit_row(
        plan_dir=plan_dir,
        plan_slug=plan_slug or "splock",
        transition_from="ready",
        transition_to="deferred",
        reason=f"outstanding_appended: {description} [line_id={line_id}]",
        emitted_by=log_emit.EMIT_OUTSTANDING,
        task_id=task_id or None,
        extra={
            "event_type": "outstanding_appended",
            "line_id": line_id,
            "session_count_after": new_count,
        },
    )

    if json_output:
        print(json.dumps({
            "result": "appended",
            "type": "outstanding",
            "line_id": line_id,
            "session_count_after": new_count,
            "machine_count_last_hour": machine_count,
        }))
    else:
        print(f"outstanding appended: line_id={line_id}")
        print(f"  session count: {new_count}/{threshold}")
    return EXIT_OK
