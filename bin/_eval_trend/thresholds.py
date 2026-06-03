"""Closed-enum threshold matrix per plan §J.10 / implplan §J.impl.10.

ANCHOR — operator-as-terminator. Threshold breach surfaces a row to
morning-review for OPERATOR REVIEW. No meta-scorer judges the
operator's labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


SAMPLE_SIZE_FLOOR = 20


@dataclass(frozen=True)
class ThresholdRule:
    scorer_id: str
    metric: str  # one of "fp_rate", "fn_rate", "spearman_rho"
    op: str  # ">" or "<"
    threshold: float
    sample_floor: int = SAMPLE_SIZE_FLOOR


# Closed enum per §J.impl.10 table (lines ~7091-7097).
THRESHOLD_MATRIX: tuple[ThresholdRule, ...] = (
    ThresholdRule(
        scorer_id="sonnet_review_R4_tampering",
        metric="fp_rate",
        op=">",
        threshold=0.10,
    ),
    ThresholdRule(
        scorer_id="sonnet_review_R4_tampering",
        metric="fn_rate",
        op=">",
        threshold=0.05,
    ),
    ThresholdRule(
        scorer_id="ralph_verifier",
        metric="fn_rate",
        op=">",
        threshold=0.05,
    ),
    ThresholdRule(
        scorer_id="sonnet_review_R5_confidence",
        metric="spearman_rho",
        op="<",
        threshold=0.30,
    ),
)


@dataclass(frozen=True)
class SkipResult:
    reason: str
    n_labels: int


@dataclass(frozen=True)
class BreachResult:
    rule: ThresholdRule
    observed_value: float
    n_labels: int


CheckResult = SkipResult | BreachResult | None  # None = no breach


def check(
    rule: ThresholdRule,
    *,
    observed_value: float,
    n_labels: int,
) -> CheckResult:
    """Apply rule. Return SkipResult under floor, BreachResult on breach,
    else None.
    """
    if n_labels < rule.sample_floor:
        return SkipResult(reason="insufficient_sample", n_labels=n_labels)
    breached = (
        observed_value > rule.threshold
        if rule.op == ">"
        else observed_value < rule.threshold
    )
    if breached:
        return BreachResult(
            rule=rule, observed_value=observed_value, n_labels=n_labels
        )
    return None


def rules_for_scorer(scorer_id: str) -> list[ThresholdRule]:
    return [r for r in THRESHOLD_MATRIX if r.scorer_id == scorer_id]


__all__ = [
    "SAMPLE_SIZE_FLOOR",
    "ThresholdRule",
    "THRESHOLD_MATRIX",
    "SkipResult",
    "BreachResult",
    "CheckResult",
    "check",
    "rules_for_scorer",
]
