"""Archive-move semantics (implplan §H.impl.7).

`maybe_move_if_all_closed(daily_file)` — move the daily file into
`<plan_dir>/morning-review/archive/<name>` iff every entry's
`Operator triage:` mirror is terminal. Returns `"moved"` / `"no_op"`.

`gc` subcommand: operator-driven batch cleanup over older daily files.

Same-filesystem path: `os.replace` (atomic rename). Cross-filesystem
fallback: `shutil.copy2` + `os.replace` + `unlink`.
"""

from __future__ import annotations

import datetime
import errno
import os
import pathlib
import shutil
from typing import Literal

from . import entry_format, log_emit, queue_file


MoveResult = Literal["moved", "no_op"]


def all_entries_terminal(daily_file: pathlib.Path) -> bool:
    """True iff every entry in the file has a terminal mirror.

    Empty file (no entries) → returns False (no entries to close means no
    archive trigger; only files with entries that have ALL been triaged
    archive).
    """
    text = queue_file.read_daily(daily_file)
    if not text.strip():
        return False
    entries = entry_format.parse(text, warn_hook=lambda _msg: None)
    if not entries:
        return False
    return all(e.triage_mirror in entry_format.TERMINAL_MIRRORS for e in entries)


def maybe_move_if_all_closed(daily_file: pathlib.Path) -> MoveResult:
    """Per §H.impl.7 algorithm.

    Returns:
      "moved" — file moved to archive/<name>
      "no_op" — at least one entry still `[pending]`, or file missing

    On move success: emits `morning_review_archived` row to the plan's
    `_orchestrator_log.jsonl`.

    Raises OSError on archive-move failure (cross-filesystem fallback
    exhausted); caller maps to EXIT_ARCHIVE_MOVE_FAILED.
    """
    if not daily_file.exists():
        return "no_op"
    if not all_entries_terminal(daily_file):
        return "no_op"

    entries = entry_format.parse(
        queue_file.read_daily(daily_file), warn_hook=lambda _msg: None
    )
    target = daily_file.parent / "archive" / daily_file.name
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        os.replace(str(daily_file), str(target))
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        # Cross-filesystem fallback per §H.impl.7 step 4 bullet 2.
        tmp_target = target.parent / f".{target.name}.tmp"
        shutil.copy2(str(daily_file), str(tmp_target))
        os.replace(str(tmp_target), str(target))
        daily_file.unlink()

    # Plan dir is the daily file's parent.parent (docs/plans/<slug>/morning-review/X.md).
    plan_dir = daily_file.parent.parent
    slug = plan_dir.name
    try:
        log_emit.emit_archived(
            plan_dir,
            slug=slug,
            daily_file=daily_file.name,
            entry_count=len(entries),
        )
    except Exception:
        # Log emission is best-effort — the move is the source of truth.
        pass
    return "moved"


def gc(
    repo_root: pathlib.Path,
    *,
    older_than_days: int = 30,
    dry_run: bool = False,
) -> tuple[list[pathlib.Path], list[pathlib.Path]]:
    """Walk every plan's `morning-review/` dir; archive eligible files.

    Returns (moved, skipped) — lists of daily-file paths.

    Eligibility:
      1. File mtime older than `older_than_days` ago, AND
      2. Every entry's mirror is terminal.

    Per plan §H.5 paragraph 2 + §H.impl.7 spec. Never deletes content;
    only moves.
    """
    moved: list[pathlib.Path] = []
    skipped: list[pathlib.Path] = []
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=older_than_days
    )
    cutoff_ts = cutoff.timestamp()

    for daily in queue_file.iter_open_daily_files(repo_root, slug=None):
        try:
            mtime = daily.stat().st_mtime
        except OSError:
            continue
        if mtime > cutoff_ts:
            # Not old enough.
            skipped.append(daily)
            continue
        if not all_entries_terminal(daily):
            skipped.append(daily)
            continue
        if dry_run:
            moved.append(daily)
            continue
        result = maybe_move_if_all_closed(daily)
        if result == "moved":
            moved.append(daily)
        else:
            skipped.append(daily)
    return moved, skipped


__all__ = [
    "MoveResult",
    "all_entries_terminal",
    "maybe_move_if_all_closed",
    "gc",
]
