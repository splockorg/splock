"""Atomic write helper — `tempfile.NamedTemporaryFile + os.replace`.

Per implplan cross-cutting conventions (lines 280-284): every JSON file
written by splock substrate uses write-temp + atomic rename.
This module is the shared Python implementation; §A.impl, §B.impl,
§C.impl, and §E.impl all import `write_atomic` from here.

Discipline (do not deviate):
- Tempfile created in the SAME directory as the target so `os.replace`
  is atomic on POSIX (cross-filesystem rename would not be atomic).
- `delete=False` on the NamedTemporaryFile + manual unlink in the except
  branch so a crash mid-write leaves no torn target file.
- `os.replace` (not `shutil.move`) so the rename is a single syscall.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class AtomicWriteError(OSError):
    """Raised when the temp-then-rename sequence fails.

    The caller (typically `main.py`) maps this to exit code 7
    (`EXIT_ATOMIC_WRITE_FAILED`) per implplan §B.impl.4 line 1156.
    """


def write_atomic(target: Path, content: str) -> None:
    """Write `content` to `target` atomically.

    On POSIX, `os.replace` of a same-filesystem temp file is atomic.
    The previous content of `target` is preserved until the rename
    completes, so a crash mid-write leaves the previous version intact
    (the tempfile is unlinked in the except branch).

    Raises:
        AtomicWriteError: if the temp file or rename fails. Target is
            untouched in this case.
    """
    target = Path(target)
    parent = target.parent
    if not parent.exists():
        raise AtomicWriteError(
            f"parent directory does not exist: {parent}"
        )

    tmp_path: Path | None = None
    try:
        # `delete=False` so we control unlink; same-dir for atomic rename.
        fd = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(parent),
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        )
        tmp_path = Path(fd.name)
        try:
            fd.write(content)
            fd.flush()
            os.fsync(fd.fileno())
        finally:
            fd.close()
        # NamedTemporaryFile defaults to 0600 (mkstemp security default);
        # os.replace preserves the temp file's mode. Set the target's mode
        # to honor the process umask so artifacts are operator-readable.
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp_path, 0o666 & ~umask)
        os.replace(tmp_path, target)
        tmp_path = None  # ownership transferred to target
    except OSError as exc:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                # Cleanup best-effort; surface the original error.
                pass
        raise AtomicWriteError(str(exc)) from exc
