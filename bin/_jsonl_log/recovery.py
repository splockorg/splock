"""JSONL corruption recovery — `_validate_or_truncate_last_line`.

Per implplan §C.impl.8 and plan §C.5. Invoked by `writer.append_row` at
step 4 of the operation sequence (post-flock, pre-schema-validation).

Algorithm
---------
1. If the file is missing or empty → no-op.
2. Seek to EOF; read backwards accumulating bytes until the most recent
   newline (or BOF, if file has no newlines yet).
3. Attempt `json.loads` on the trailing partial line.
4. If parse succeeds → no-op.
5. If parse fails:
   a. Capture the failed bytes as `_corrupt_bytes_hex` (via `.hex()`).
   b. Truncate the file to the position of the prior newline via
      `os.ftruncate`. If no prior newline exists, truncate to zero.
   c. Append a `_corrupt_truncated` audit row by direct call to the
      shared `_append_one_row_unlocked` helper from writer.py. We do
      NOT recurse through `append_row` (that would attempt to
      re-acquire the flock we already hold). The audit row carries
      `emitted_by="_validate_or_truncate_last_line"`, fixed
      `session_id="_recovery"`, and additive `_corrupt_bytes_hex` +
      `_truncated_byte_count` fields.
   d. Return `("truncated", corrupt_bytes_hex, truncated_byte_count)`.

Recovery does NOT handle mid-file corruption, schema-version drift, or
cascading multi-line corruption (each cycle handles ONE trailing
line). See §C.impl.8 "What recovery does NOT handle".
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
import pathlib
import socket
from typing import Literal


# Read-back chunk size for the seek-EOF-backwards scan. The trailing
# partial line in a normal corruption scenario is <8 KB (a row); we use
# a 16 KB chunk to amortize across rare large reasons.
_READBACK_CHUNK = 16 * 1024


@dataclasses.dataclass(frozen=True)
class ValidationResult:
    """Result of `_validate_or_truncate_last_line`."""

    action: Literal["no_op", "truncated"]
    corrupt_bytes_hex: str | None
    truncated_byte_count: int


def _find_trailing_line(path: pathlib.Path) -> tuple[bytes, int, int] | None:
    """Return (trailing_bytes, prior_newline_offset, file_size) or None.

    Walks backwards from EOF chunk-by-chunk until either the most recent
    newline is found or BOF is reached. The returned trailing bytes are
    everything AFTER `prior_newline_offset` (exclusive) up to EOF — i.e.,
    the candidate last line, without its leading newline.

    If the file has no trailing data (empty, or ends with a single
    newline with no characters after it), returns None.

    `prior_newline_offset` is the byte offset OF the trailing newline
    (i.e., truncating to this offset KEEPS the prior line's newline and
    drops everything after).
    """
    size = path.stat().st_size
    if size == 0:
        return None
    with path.open("rb") as fh:
        # Start from EOF; scan backwards.
        pos = size
        accumulated = b""
        while pos > 0:
            read_start = max(0, pos - _READBACK_CHUNK)
            fh.seek(read_start)
            chunk = fh.read(pos - read_start)
            pos = read_start
            # Search for newline in chunk (scanning right-to-left).
            idx = chunk.rfind(b"\n")
            if idx == -1:
                # No newline in this chunk; prepend and keep walking.
                accumulated = chunk + accumulated
                continue
            # Newline at chunk[idx]. Prior-newline offset is the absolute
            # offset of that newline byte; trailing = chunk[idx+1:] + accumulated.
            prior_newline_offset = read_start + idx
            trailing = chunk[idx + 1 :] + accumulated
            if not trailing:
                # File ends cleanly with a newline; no trailing partial.
                return None
            return (trailing, prior_newline_offset, size)
        # Walked to BOF without finding a newline: whole file is one
        # trailing line.
        if not accumulated:
            return None
        return (accumulated, -1, size)


def _validate_or_truncate_last_line(path: pathlib.Path) -> ValidationResult:
    """Per implplan §C.impl.8. See module docstring."""
    if not path.exists():
        return ValidationResult(action="no_op", corrupt_bytes_hex=None, truncated_byte_count=0)
    if path.stat().st_size == 0:
        return ValidationResult(action="no_op", corrupt_bytes_hex=None, truncated_byte_count=0)

    info = _find_trailing_line(path)
    if info is None:
        # File ends cleanly with newline; nothing to validate at tail.
        return ValidationResult(action="no_op", corrupt_bytes_hex=None, truncated_byte_count=0)
    trailing, prior_newline_offset, file_size = info

    # Try to parse the trailing line.
    try:
        json.loads(trailing.decode("utf-8"))
        return ValidationResult(action="no_op", corrupt_bytes_hex=None, truncated_byte_count=0)
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass

    # Parse failed; capture forensic bytes, truncate, and append audit row.
    corrupt_bytes_hex = trailing.hex()
    truncated_byte_count = len(trailing)

    if prior_newline_offset == -1:
        # No prior newline; whole file is corrupt trailing data. Truncate to zero.
        new_size = 0
    else:
        # Keep through the prior newline (inclusive); drop everything after it.
        new_size = prior_newline_offset + 1

    with path.open("rb+") as fh:
        fh.truncate(new_size)
        fh.flush()
        os.fsync(fh.fileno())

    # Now append the `_corrupt_truncated` audit row. We are called from
    # inside writer.append_row's flock-held critical section, so we use
    # the unlocked append helper to avoid recursive flock acquisition
    # (fcntl.flock is non-reentrant on Linux with separately-opened fds).
    # Per §C mid-section review MAJ-2: explicitly validate the audit row
    # against the schema before the unlocked write, preserving §C.impl.5
    # step 5's at-write enforcement that the unlocked path would skip.
    from .writer import _append_one_row_unlocked
    from .schema import validate_row

    plan_slug = path.parent.name
    audit_row: dict = {
        "schema_version": 1,
        "ts": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_id": "_recovery",
        "emitted_by": "_validate_or_truncate_last_line",
        "plan_slug": plan_slug,
        "chain_id": None,
        "task_id": None,
        "transition": {"from": "unknown", "to": "unknown"},
        "pointer": None,
        "retry_count": None,
        "mode_at_transition": {"overnight": None, "guardrail": None},
        "override_in_effect": None,
        "reason": "corrupt line truncated; original bytes recorded in _corrupt_bytes_hex",
        "verifier_verdict_ref": None,
        "writer_pid": os.getpid(),
        "writer_host": socket.gethostname(),
        "_corrupt_bytes_hex": corrupt_bytes_hex,
        "_truncated_byte_count": truncated_byte_count,
    }
    validate_row(audit_row)
    _append_one_row_unlocked(path, audit_row)

    return ValidationResult(
        action="truncated",
        corrupt_bytes_hex=corrupt_bytes_hex,
        truncated_byte_count=truncated_byte_count,
    )
