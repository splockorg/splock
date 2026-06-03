"""Per-iteration Sonnet-input appender for §G.4 chain-test-file-edit-flag.

Per implplan §G.impl.6. The chain-test-file-edit-flag hook appends a
structured flag entry to a per-iteration JSONL staging file the chain
driver maintains for retry-loop bookkeeping; Sonnet rubric R4 reads
this file when scoring tampering risk.

File path: `<plan_dir>/_sonnet_input_iter<N>_test_edits.jsonl`.

(Note: the §F-shipped hook `chain-test-file-edit-flag.sh` already writes
this format inline. This module exists for hook scripts under §G that
need to share the same write contract, e.g., other detection-only
PostToolUse hooks that may be added later.)
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any


def append_flag(
    plan_dir: Path,
    iter_n: int,
    payload: dict[str, Any],
) -> Path:
    """Append a flag entry to the per-iteration staging file.

    Returns the path written to (for caller telemetry).
    """
    staging = plan_dir / f"_sonnet_input_iter{iter_n}_test_edits.jsonl"
    staging.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload.setdefault("ts", _now_iso_z())
    line = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with staging.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return staging


def _now_iso_z() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


__all__ = [
    "append_flag",
]
