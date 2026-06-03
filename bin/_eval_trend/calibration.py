"""Calibration math wiring for `bin/eval-trend` (§J.impl.10).

Joins emission + label rows from `_scores.jsonl` by `score_id ↔
score_id_ref` and applies binary / ordinal calibration via
`bin._eval_common.calibration_math`.
"""

from __future__ import annotations

import pathlib
from typing import Optional

from bin._eval_common import calibration_math, score_writer
from bin._eval_common.scorer_registry import SCORER_KIND


def joined_labels_for_scorer(
    plan_dir: pathlib.Path, scorer_id: str
) -> list[tuple[dict, dict]]:
    """Return [(emission_row, label_row), ...] joined by score_id <-> ref.

    Filters to the given `scorer_id`. Excludes baseline-mode emissions
    (those carry `scorer_attributes.baseline_name`).
    """
    emissions_by_id: dict[str, dict] = {}
    labels_by_ref: dict[str, dict] = {}
    for row in score_writer.iter_rows(plan_dir):
        if row.get("row_type") == "emission" and row.get("scorer_id") == scorer_id:
            attrs = row.get("scorer_attributes") or {}
            if "baseline_name" in attrs or "replay_case_id" in attrs:
                continue
            emissions_by_id[row["score_id"]] = row
        elif row.get("row_type") == "label":
            labels_by_ref[row["score_id_ref"]] = row
    out: list[tuple[dict, dict]] = []
    for sid, emission in emissions_by_id.items():
        if sid in labels_by_ref:
            out.append((emission, labels_by_ref[sid]))
    return out


def binary_stats_for(
    plan_dir: pathlib.Path, scorer_id: str
) -> Optional[calibration_math.BinaryStats]:
    if SCORER_KIND.get(scorer_id) != "binary":
        return None
    joined = joined_labels_for_scorer(plan_dir, scorer_id)
    pairs = [
        (e.get("score_category", ""), l.get("ground_truth_label", ""))
        for e, l in joined
    ]
    return calibration_math.binary_calibration(pairs)


def ordinal_stats_for(
    plan_dir: pathlib.Path, scorer_id: str
) -> Optional[calibration_math.OrdinalStats]:
    if SCORER_KIND.get(scorer_id) != "ordinal":
        return None
    joined = joined_labels_for_scorer(plan_dir, scorer_id)
    label_score_map = {
        "true-positive": 1.0,
        "true-negative": 1.0,
        "false-positive": 0.0,
        "false-negative": 0.0,
    }
    pairs: list[tuple[float, float]] = []
    for e, l in joined:
        gt = label_score_map.get(l.get("ground_truth_label", ""))
        if gt is None:
            continue
        sv = e.get("score_value")
        try:
            sv_num = float(sv)
        except (TypeError, ValueError):
            continue
        pairs.append((sv_num, gt))
    return calibration_math.ordinal_calibration(pairs)


def numeric_trend_for(
    plan_dir: pathlib.Path, scorer_id: str
) -> Optional[calibration_math.NumericStats]:
    if SCORER_KIND.get(scorer_id) != "numeric":
        return None
    vals: list[float] = []
    for row in score_writer.iter_rows(plan_dir):
        if row.get("row_type") == "emission" and row.get("scorer_id") == scorer_id:
            sv = row.get("score_value")
            try:
                vals.append(float(sv))
            except (TypeError, ValueError):
                continue
    return calibration_math.numeric_trend(vals)


__all__ = [
    "joined_labels_for_scorer",
    "binary_stats_for",
    "ordinal_stats_for",
    "numeric_trend_for",
]
