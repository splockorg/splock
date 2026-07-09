"""qa subagent invocation substrate (splock v2.7 §D.8.3).

Public surface:
- `invoke_qa(slug, inputs, ...)` — Python callable used by an in-process
  caller (e.g., future chain driver if/when qa lands in the pipeline);
  parallels `bin._planner.invoke_planner`.
- `QaInputs` / `QaResult` dataclasses — typed I/O.
- `QaSdkFailed` — raised on SDK errors.
- `RUBRIC_MD` — re-export of the deterministically-constructed qa rubric
  (plan §D.8.3 prescribes a deterministic rubric; this module's constant
  IS that determinism source).

The CLI surface lives in `main.py` and is dispatched via the POSIX
wrapper at `bin/qa`. Both call paths land in `invoke_qa`; the CLI writes
the resulting MD to disk (driver-writes-not-subagent invariant per plan
§D.6 criterion 5 — same discipline as the planner CLI).

Single-call by design: qa output is structured MD (block A/B/C/D), not
JSON, so the two-call constrained-decoding mechanism that backs `/plan`
and `/implplan` does not apply. The block structure is enforced via the
deterministic rubric in `rubric.py`, not via response_format.
"""

from __future__ import annotations

from .invoke import (
    QaInputs,
    QaResult,
    QaSdkFailed,
    invoke_qa,
)
from .rubric import RUBRIC_MD

__all__ = [
    "QaInputs",
    "QaResult",
    "QaSdkFailed",
    "RUBRIC_MD",
    "invoke_qa",
]
