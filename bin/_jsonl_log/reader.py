"""Corruption-aware line-by-line reader for `_orchestrator_log.jsonl`.

Consumers (`bin/render_log`, `bin/state-divergence-check`,
morning-report aggregator, etc.) read JSONL through this module rather
than calling `json.loads` themselves so the corruption-tolerance policy
is uniform.

Behavior
--------
- Each row is parsed with `json.loads`.
- Parse failure: yielded as `CorruptRow(line_number, raw_bytes)` rather
  than raising. Consumers decide whether to abort or continue based on
  their own contract (see §C.impl.4 — divergence-check exits 2 on parse
  failure; §C.impl.10 — render_log emits a `_corrupt` marker MD line
  and continues).
- The reader does NOT modify the file. Recovery is exclusively the
  writer's responsibility (§C.impl.8).
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Iterator, Union


@dataclasses.dataclass(frozen=True)
class CorruptRow:
    """A line that failed `json.loads`. Carries 1-indexed line number +
    raw bytes for forensics."""

    line_number: int
    raw_bytes: bytes


Row = Union[dict, CorruptRow]


def iter_rows(path: pathlib.Path) -> Iterator[Row]:
    """Yield each row in the JSONL.

    Empty / missing file → yields nothing. Each yielded value is either
    a `dict` (parsed JSON object) or a `CorruptRow` (parse failed).

    Line numbers are 1-indexed to match `bin/render_log`'s suffix
    convention (`... (full text in JSONL line N)`).
    """
    if not path.exists():
        return
    with path.open("rb") as fh:
        for lineno, raw in enumerate(fh, start=1):
            # Strip trailing newline only; preserve any other bytes verbatim.
            stripped = raw.rstrip(b"\n")
            if not stripped:
                continue  # blank line; tolerate
            try:
                row = json.loads(stripped.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                yield CorruptRow(line_number=lineno, raw_bytes=stripped)
                continue
            if not isinstance(row, dict):
                # JSONL rows MUST be objects; a bare JSON value (array,
                # string, number) at the line level violates the contract.
                yield CorruptRow(line_number=lineno, raw_bytes=stripped)
                continue
            yield row


def read_rows(path: pathlib.Path) -> list[Row]:
    """Convenience: materialize all rows into a list."""
    return list(iter_rows(path))
