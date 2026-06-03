"""CLI entry for `bin/git-merge-jsonl`.

Per implplan §C.impl.9 lines 1884-1888. Git invokes the merge driver
with the signature:

    bin/git-merge-jsonl %O %A %B %L

where:
    %O — ancestor file path
    %A — ours / current path (driver writes merged content in-place)
    %B — theirs / other path
    %L — conflict-marker-size (unused)

Exit codes per git merge-driver protocol:
    0 success — merged content written to %A
    1 conflict / merge impossible — diagnostic to stderr

The driver writes to %A via atomic write-temp + rename to avoid
partial-state writes if interrupted mid-merge (cross-cutting "Atomic
write discipline" in implplan).
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
from typing import Sequence

from .merge import MergeImpossibleError, merge


def _atomic_replace(target: pathlib.Path, body: bytes) -> None:
    """Write `body` to `target` atomically (write-temp + rename)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=target.parent,
        prefix=".__git_merge_jsonl__",
        suffix=".tmp",
        delete=False,
    )
    try:
        tmp.write(body)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        pathlib.Path(tmp.name).replace(target)
    except Exception:
        tmp.close()
        try:
            pathlib.Path(tmp.name).unlink(missing_ok=True)
        except Exception:
            pass
        raise


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if len(args) < 3:
        print(
            "usage: bin/git-merge-jsonl %O %A %B [%L]",
            file=sys.stderr,
        )
        return 1
    ancestor_path = pathlib.Path(args[0])
    ours_path = pathlib.Path(args[1])
    theirs_path = pathlib.Path(args[2])
    # %L (args[3]) is unused.

    try:
        ancestor_bytes = (
            ancestor_path.read_bytes() if ancestor_path.exists() else b""
        )
        ours_bytes = ours_path.read_bytes() if ours_path.exists() else b""
        theirs_bytes = (
            theirs_path.read_bytes() if theirs_path.exists() else b""
        )
    except OSError as exc:
        print(f"git-merge-jsonl: read error: {exc}", file=sys.stderr)
        return 1
    try:
        merged = merge(ancestor_bytes, ours_bytes, theirs_bytes)
    except MergeImpossibleError as exc:
        print(f"git-merge-jsonl: merge impossible: {exc}", file=sys.stderr)
        return 1
    try:
        _atomic_replace(ours_path, merged)
    except OSError as exc:
        print(f"git-merge-jsonl: write error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
