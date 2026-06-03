"""Per-case baseline-vs-current comparison (§J.impl.9 + §J.impl.7).

A "regression" is a case where current score is worse than baseline per
the case's `expected_outcome`. The v2.7 implementation supports the
deterministic case: regression cases not present in baseline are
counted; cases whose expected_outcome mismatches between baseline and
case file are flagged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class CompareResult:
    regressions: tuple[str, ...]  # case_ids that regressed
    missing: tuple[str, ...]  # case_ids present in cases but absent from baseline
    extra: tuple[str, ...]  # baseline case_ids no longer in active set
    matched: int  # case_ids matching baseline (no regression)


def compare(
    *,
    baseline_case_rows: Iterable[dict],
    active_cases: Iterable[dict],
) -> CompareResult:
    """Compare current active regression cases to the baseline case_scores.

    Returns lists of regressed/missing/extra case ids.
    """
    baseline_by_case: dict[str, dict] = {}
    for row in baseline_case_rows:
        case_id = (
            row.get("task_id")
            or row.get("scorer_attributes", {}).get("case_id")
        )
        if not case_id:
            continue
        baseline_by_case[case_id] = row

    active_by_case: dict[str, dict] = {c["case_id"]: c for c in active_cases}

    regressions: list[str] = []
    missing: list[str] = []
    matched = 0
    for case_id, current in active_by_case.items():
        b = baseline_by_case.get(case_id)
        if b is None:
            missing.append(case_id)
            continue
        baseline_expected = b.get("scorer_attributes", {}).get("expected_outcome")
        current_expected = current.get("expected_outcome")
        if baseline_expected != current_expected:
            regressions.append(case_id)
        else:
            matched += 1

    extra = [c for c in baseline_by_case if c not in active_by_case]

    return CompareResult(
        regressions=tuple(regressions),
        missing=tuple(missing),
        extra=tuple(extra),
        matched=matched,
    )


__all__ = ["CompareResult", "compare"]
