"""Calibration math for §J.impl.10 — scorer-quality metrics.

Per splock implplan §J.impl.10. Termination-point anchor
(§4a.2): these metrics are DIAGNOSTIC. The operator reads the threshold
breaches in morning-review and decides whether to revise the scorer's
prompt, accept drift, or escalate. There is no meta-scorer above the
operator's ground-truth labels.

Binary scorers compute FP-rate, FN-rate, accuracy. Ordinal scorers
compute Spearman ρ. Free-text scorers have no automated metric — they
are surfaced via `bin/eval-trend --recent N`. Numeric scorers are
trend-only (rolling avg, max, p95).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence


@dataclass(frozen=True)
class BinaryStats:
    n_labels: int
    fp_rate: float  # false_positive ÷ (predicted_positive_total)
    fn_rate: float  # false_negative ÷ (predicted_negative_total)
    accuracy: float  # (TP + TN) ÷ total_labeled
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int


@dataclass(frozen=True)
class OrdinalStats:
    n_labels: int
    spearman_rho: float


@dataclass(frozen=True)
class NumericStats:
    n: int
    mean: float
    p95: float
    max: float


def _safe_div(num: int, denom: int) -> float:
    return (num / denom) if denom else 0.0


def binary_calibration(
    pairs: Sequence[tuple[str, str]],
) -> BinaryStats:
    """Compute binary-scorer calibration metrics over (score_category, label) pairs.

    Convention: a `flagged` / `fail` emission is "predicted positive";
    a `pass` emission is "predicted negative". Ground-truth labels are
    {true-positive, false-positive, true-negative, false-negative, n/a}.
    The `n/a` pairs are skipped from the sample (consistent with §J.impl.8
    matrix where deferral verdicts attach `n/a`).
    """
    tp = fp = tn = fn = 0
    for cat, lbl in pairs:
        if lbl == "n/a":
            continue
        if lbl == "true-positive":
            tp += 1
        elif lbl == "false-positive":
            fp += 1
        elif lbl == "true-negative":
            tn += 1
        elif lbl == "false-negative":
            fn += 1
    n = tp + fp + tn + fn
    predicted_positive = tp + fp
    predicted_negative = tn + fn
    return BinaryStats(
        n_labels=n,
        fp_rate=_safe_div(fp, predicted_positive),
        fn_rate=_safe_div(fn, predicted_negative),
        accuracy=_safe_div(tp + tn, n),
        true_positive=tp,
        false_positive=fp,
        true_negative=tn,
        false_negative=fn,
    )


def spearman_rho(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Spearman rank correlation. Returns 0.0 on degenerate input.

    Implementation: rank xs and ys (average ranks for ties), then Pearson
    ρ on the ranks.
    """
    n = len(xs)
    if n != len(ys) or n < 2:
        return 0.0

    def _avg_ranks(seq: Sequence[float]) -> list[float]:
        idx_sorted = sorted(range(len(seq)), key=lambda i: seq[i])
        ranks = [0.0] * len(seq)
        i = 0
        while i < len(seq):
            j = i
            while j + 1 < len(seq) and seq[idx_sorted[j + 1]] == seq[idx_sorted[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0  # 1-indexed average rank
            for k in range(i, j + 1):
                ranks[idx_sorted[k]] = avg
            i = j + 1
        return ranks

    rx = _avg_ranks(xs)
    ry = _avg_ranks(ys)
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n
    num = sum((a - mean_rx) * (b - mean_ry) for a, b in zip(rx, ry))
    denom_x = math.sqrt(sum((a - mean_rx) ** 2 for a in rx))
    denom_y = math.sqrt(sum((b - mean_ry) ** 2 for b in ry))
    if denom_x == 0.0 or denom_y == 0.0:
        return 0.0
    return num / (denom_x * denom_y)


def ordinal_calibration(
    pairs: Sequence[tuple[float, float]],
) -> OrdinalStats:
    """Spearman ρ for ordinal scorers. `pairs[i] = (scorer_value,
    ground_truth_score)`.

    Caller pre-maps `ground_truth_label` to numeric scores (e.g.
    true-positive=1, false-positive=0, n/a→skip).
    """
    filtered = [(x, y) for x, y in pairs]
    if len(filtered) < 2:
        return OrdinalStats(n_labels=len(filtered), spearman_rho=0.0)
    xs = [x for x, _ in filtered]
    ys = [y for _, y in filtered]
    return OrdinalStats(n_labels=len(filtered), spearman_rho=spearman_rho(xs, ys))


def numeric_trend(values: Iterable[float]) -> NumericStats:
    """Rolling stats for numeric scorers (cost_per_phase, wall_time_per_phase)."""
    vs = sorted(float(v) for v in values)
    n = len(vs)
    if n == 0:
        return NumericStats(n=0, mean=0.0, p95=0.0, max=0.0)
    mean = sum(vs) / n
    p95_index = int(math.ceil(0.95 * n)) - 1
    p95_index = max(0, min(p95_index, n - 1))
    return NumericStats(n=n, mean=mean, p95=vs[p95_index], max=vs[-1])


__all__ = [
    "BinaryStats",
    "OrdinalStats",
    "NumericStats",
    "binary_calibration",
    "spearman_rho",
    "ordinal_calibration",
    "numeric_trend",
]
