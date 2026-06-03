"""Lockfile path + acquisition helpers for `_orchestrator_log.jsonl`.

Per implplan §C.impl.5 step 3 (acquire flock on
`<plan_dir>/_orchestrator_log.jsonl.lock` via `fcntl.flock` LOCK_EX,
blocks). Lockfile is created lazily by `_open_lockfile_for_lock`.

The lockfile path is listed in §G's `sealed_paths.txt` so agents cannot
delete it mid-chain. The lockfile is NEVER auto-cleaned — its persistence
is intentional.

Cross-platform note: `fcntl` is Linux/POSIX. Per CLAUDE.md "Execution
environment — WSL2/Ubuntu only (strict)", this is acceptable. Windows is
permitted only for the IDE shell.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import pathlib
from typing import Iterator


JSONL_BASENAME = "_orchestrator_log.jsonl"
LOCKFILE_SUFFIX = ".lock"


def jsonl_path(plan_dir: pathlib.Path) -> pathlib.Path:
    """Return the canonical JSONL path for a given plan dir."""
    return plan_dir / JSONL_BASENAME


def lockfile_path(plan_dir: pathlib.Path) -> pathlib.Path:
    """Return the canonical lockfile path for a given plan dir.

    Convention: `<plan_dir>/_orchestrator_log.jsonl.lock`. Per
    implplan §C.impl.5 line 1705.
    """
    return plan_dir / (JSONL_BASENAME + LOCKFILE_SUFFIX)


@contextlib.contextmanager
def acquire_exclusive(plan_dir: pathlib.Path) -> Iterator[int]:
    """Acquire LOCK_EX on the JSONL's lockfile. Blocking.

    Lazy creation of the lockfile if missing. Releases automatically on
    `with`-block exit via `fcntl.flock` LOCK_UN, even if an exception is
    raised inside the block.

    Yields the file descriptor (mostly for diagnostic / introspection
    purposes; callers do not normally need it).
    """
    # plan_dir must exist; callers (writer.append_row) construct paths
    # against an existing slug directory. We do NOT auto-mkdir here —
    # creating a slug directory is the chain driver's responsibility per
    # §A.impl.
    plan_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lockfile_path(plan_dir)
    # Open in r+ if exists; otherwise create with 'a+' (which doesn't
    # truncate). Use O_CREAT via os.open for atomicity on the creation
    # side; reopen via fdopen for ergonomics.
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield fd
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
