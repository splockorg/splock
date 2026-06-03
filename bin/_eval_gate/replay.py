"""Dataset replay → per-case score collection (§J.impl.9).

v2.7 implementation: replay loads each active case and returns a
"current" view of its expected outcome (no live LLM round trip — the
real replay scaffolding lives in `bin/_regression_replay`). The gate
compares the case's current `expected_outcome` field against the
baseline's snapshot.

Future expansion (post-AGR marker mint): replay invokes scorers via the
retry-loop substrate. v2.7 keeps the gate deterministic.
"""

from __future__ import annotations

import pathlib

from bin._eval_common import regression_case


def collect_active_cases(plan_dir: pathlib.Path) -> list[dict]:
    return regression_case.list_cases(plan_dir, include_retired=False)


__all__ = ["collect_active_cases"]
