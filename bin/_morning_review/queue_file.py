"""Daily queue-file reader / writer (implplan §H.impl.4 + cross-cutting atomic-write).

Per cross-cutting `Atomic write discipline` (implplan line 280) + `flock
discipline` (line 286). This module is the SOLE writer to
`docs/plans/<slug>/morning-review/<YYYY-MM-DD>.md` from the CLI surface;
the §F.impl.7 halt-handoff caller writes via its own atomic path through
`bin/_render_plan.atomic_write.write_atomic`.

Discipline:
1. Read-modify-write cycles wrap in `flock` on `<daily_file>.lock`.
2. Writes go through write-temp + `os.replace` (`atomic_write`).
3. Bootstrap is idempotent: re-invoking `--internal-bootstrap-day` on
   an existing file is a no-op.
"""

from __future__ import annotations

import datetime
import errno
import fcntl
import os
import pathlib
import tempfile
from contextlib import contextmanager
from typing import Iterator, List, Optional

from . import entry_format
from .entry_format import Entry


def daily_file_path(plan_dir: pathlib.Path, date_iso: str) -> pathlib.Path:
    """Compute `<plan_dir>/morning-review/<YYYY-MM-DD>.md` (does not check
    existence)."""
    return plan_dir / "morning-review" / f"{date_iso}.md"


def lock_file_path(daily_file: pathlib.Path) -> pathlib.Path:
    """Lockfile path: `<daily_file>.lock`."""
    return daily_file.parent / f"{daily_file.name}.lock"


@contextmanager
def daily_flock(daily_file: pathlib.Path) -> Iterator[None]:
    """Acquire an exclusive flock on `<daily_file>.lock`.

    The lockfile parent is `mkdir`'d if missing. The lockfile itself is
    created if absent; we do not unlink it on release (cheap, and avoids
    EEXIST races on next acquire).
    """
    lock = lock_file_path(daily_file)
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock), os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def atomic_write(target: pathlib.Path, text: str) -> None:
    """Write `text` to `target` via temp-file + `os.replace`.

    Both `tempfile.NamedTemporaryFile(dir=target.parent)` and
    `os.replace` are required per cross-cutting line 280.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    # delete=False so we can rename after closing — context-manager cleanup
    # would unlink on the way out otherwise.
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
    except Exception:
        # Best-effort cleanup; do not mask the original exception.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_daily(daily_file: pathlib.Path) -> str:
    """Return the file text, or empty string if missing."""
    try:
        return daily_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def write_daily(daily_file: pathlib.Path, text: str) -> None:
    """Atomic full-file write, with flock held for the duration."""
    with daily_flock(daily_file):
        atomic_write(daily_file, text)


def parse_daily(daily_file: pathlib.Path, *, warn_hook=None) -> List[Entry]:
    """Parse the daily file (empty list if missing)."""
    text = read_daily(daily_file)
    if not text:
        return []
    return entry_format.parse(text, warn_hook=warn_hook)


def bootstrap_day(
    plan_dir: pathlib.Path,
    date_iso: str,
    *,
    chain_id: Optional[str] = None,
) -> pathlib.Path:
    """Mkdir-p the per-plan morning-review/ dir + write a header if absent.

    Per §H.impl.9 seam 1: the halt-handoff caller invokes
    `bin/morning-review --internal-bootstrap-day` which dispatches here.
    No-op if the file already exists (idempotent).

    Returns the daily-file path.
    """
    daily = daily_file_path(plan_dir, date_iso)
    daily.parent.mkdir(parents=True, exist_ok=True)
    if daily.exists():
        return daily
    if chain_id:
        emitter = f"{entry_format.GENERATOR_HALT_HANDOFF} {chain_id}"
    else:
        emitter = entry_format.GENERATOR_BOOTSTRAP
    header = entry_format.render_header(date_iso, emitter)
    with daily_flock(daily):
        # Re-check inside the lock — another process may have raced us.
        if daily.exists():
            return daily
        atomic_write(daily, header)
    return daily


def update_mirror_atomic(
    daily_file: pathlib.Path,
    task_id: str,
    new_mirror: str,
) -> Optional[Entry]:
    """Read-modify-write cycle: update the mirror line for `task_id`.

    Returns the matched Entry (post-update) on success, or None if no
    entry matched. The whole cycle is flock-protected per cross-cutting
    line 286.
    """
    with daily_flock(daily_file):
        text = read_daily(daily_file)
        new_text, matched = entry_format.update_triage_mirror(
            text, task_id, new_mirror
        )
        if matched is None:
            return None
        atomic_write(daily_file, new_text)
        return matched


def iter_open_daily_files(
    repo_root: pathlib.Path,
    slug: Optional[str] = None,
) -> Iterator[pathlib.Path]:
    """Yield every open (non-archived) daily file under
    `docs/plans/<slug>/morning-review/` for the given slug, or all slugs
    if `slug` is None.

    Skips files inside `archive/` subdirs.
    """
    plans_root = repo_root / "docs" / "plans"
    if not plans_root.is_dir():
        return
    if slug is not None:
        slugs = [slug]
    else:
        slugs = [p.name for p in plans_root.iterdir() if p.is_dir()]
    for s in slugs:
        mr_dir = plans_root / s / "morning-review"
        if not mr_dir.is_dir():
            continue
        for f in sorted(mr_dir.glob("*.md")):
            # `_index.md` is a derived view — not a queue file.
            if f.name == "_index.md":
                continue
            yield f


def find_entry_across_files(
    repo_root: pathlib.Path,
    slug: str,
    task_id: str,
) -> Optional[tuple[pathlib.Path, Entry]]:
    """Search all open daily files for `slug` for an entry matching
    `task_id`. Returns the (file, entry) pair on first hit, else None."""
    for daily in iter_open_daily_files(repo_root, slug):
        entry = entry_format.find_entry(read_daily(daily), task_id)
        if entry is not None:
            return daily, entry
    return None


def today_iso() -> str:
    """UTC date in `YYYY-MM-DD` form (matches halt-handoff convention)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


__all__ = [
    "daily_file_path",
    "lock_file_path",
    "daily_flock",
    "atomic_write",
    "read_daily",
    "write_daily",
    "parse_daily",
    "bootstrap_day",
    "update_mirror_atomic",
    "iter_open_daily_files",
    "find_entry_across_files",
    "today_iso",
]
