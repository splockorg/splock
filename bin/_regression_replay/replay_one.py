"""Single-case replay (§J.impl.6).

v2.7: reconstruct system inputs from the case's `case_inputs` inline
snapshots, dump them into a tempdir, then surface a side-by-side diff
between (expected_outcome_details, the case's inline outputs). The full
scorer-rerun path (invoking Sonnet/Ralph against materialized inputs)
arrives with the AGR marker.
"""

from __future__ import annotations

import pathlib
import tempfile
from dataclasses import dataclass
from typing import Optional


@dataclass
class ReplayPlan:
    case_id: str
    tempdir: pathlib.Path
    expected_outcome: str
    expected_outcome_details: dict
    materialized_files: list[pathlib.Path]


def materialize(case_payload: dict) -> ReplayPlan:
    """Write `case_inputs.*_content` strings into a tempdir.

    Returns a ReplayPlan referencing the tempdir + materialized files.
    Caller is responsible for cleanup; we use a fresh tempdir per call
    (no cross-replay state).
    """
    tdir = pathlib.Path(tempfile.mkdtemp(prefix=f"replay_{case_payload['case_id']}_"))
    materialized: list[pathlib.Path] = []
    for key, value in case_payload.get("case_inputs", {}).items():
        if not isinstance(value, str):
            continue
        # Use the key itself as the filename (sanitize a bit).
        safe = key.replace("/", "_").replace("..", "")
        target = tdir / safe
        try:
            target.write_text(value, encoding="utf-8")
        except OSError:
            continue
        materialized.append(target)
    return ReplayPlan(
        case_id=case_payload["case_id"],
        tempdir=tdir,
        expected_outcome=case_payload.get("expected_outcome", ""),
        expected_outcome_details=case_payload.get("expected_outcome_details", {}),
        materialized_files=materialized,
    )


__all__ = ["ReplayPlan", "materialize"]
