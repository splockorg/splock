"""Closed-enum scorer registry + implicit-labeling matrix.

Per splock implplan §J.impl.4 (scorer_id closed enum) and
§J.impl.8 (IMPLICIT_LABELING_MATRIX wired into §H.impl.6 post-success path
step 2a).
"""

from __future__ import annotations

from dataclasses import dataclass


SCORER_IDS: frozenset[str] = frozenset(
    {
        "ralph_verifier",
        "sonnet_review_R1_root_cause",
        "sonnet_review_R2_what_missed",
        "sonnet_review_R3_next_action",
        "sonnet_review_R4_tampering",
        "sonnet_review_R5_confidence",
        "test_runner_exit",
        "cost_per_phase",
        "wall_time_per_phase",
        # §J.impl.15 #1 ratification — E.6 telemetry hybrid scorers
        # registered at code-phase ship per schema v1 → v2 additive bump.
        "qualifying_run_count",
        "revision_path_exercised",
        "manual_correction_count",
    }
)

SCORE_CATEGORIES: frozenset[str] = frozenset(
    {"pass", "fail", "flagged", "unclear", "numeric"}
)

GROUND_TRUTH_LABELS: frozenset[str] = frozenset(
    {"true-positive", "false-positive", "true-negative", "false-negative", "n/a"}
)

GROUND_TRUTH_SOURCES: frozenset[str] = frozenset(
    {"operator_morning_review", "operator_direct", "auto_test"}
)


# Binary scorers compute FP / FN / Accuracy; ordinal scorers compute
# Spearman. Free-text scorers have no automated metric — surfaced via
# `bin/eval-trend --recent N`. Numeric scorers trend-only.
SCORER_KIND: dict[str, str] = {
    "ralph_verifier": "binary",
    "sonnet_review_R1_root_cause": "free_text",
    "sonnet_review_R2_what_missed": "free_text",
    "sonnet_review_R3_next_action": "free_text",
    "sonnet_review_R4_tampering": "binary",
    "sonnet_review_R5_confidence": "ordinal",
    "test_runner_exit": "binary",
    "cost_per_phase": "numeric",
    "wall_time_per_phase": "numeric",
    "qualifying_run_count": "numeric",
    "revision_path_exercised": "numeric",
    "manual_correction_count": "numeric",
}


# --- Implicit ground-truth labeling matrix (§J.impl.8) -------------------


@dataclass(frozen=True)
class LabelRule:
    """One row in IMPLICIT_LABELING_MATRIX.

    The triage gesture + scorer condition → implicit ground-truth label
    attached to any matching scorer emission. `label = None` means
    non-judgmental (no label written).
    """

    gesture: str  # one of "abandon", "reactivate", "route-outstanding",
    #                       "route-marker", "acknowledge"
    scorer_id: str  # one of SCORER_IDS
    scorer_value_match: str  # e.g. "yes-flagged", "fail"
    label: str | None  # one of GROUND_TRUTH_LABELS or None for non-judgmental


# Per §J.impl.8 table (lines ~6909-6918). Closed enum.
IMPLICIT_LABELING_MATRIX: tuple[LabelRule, ...] = (
    LabelRule(
        gesture="abandon",
        scorer_id="sonnet_review_R4_tampering",
        scorer_value_match="yes-flagged",
        label="true-positive",
    ),
    LabelRule(
        gesture="abandon",
        scorer_id="ralph_verifier",
        scorer_value_match="fail",
        label="true-positive",
    ),
    LabelRule(
        gesture="reactivate",
        scorer_id="sonnet_review_R4_tampering",
        scorer_value_match="yes-flagged",
        label="false-positive",
    ),
    LabelRule(
        gesture="reactivate",
        scorer_id="ralph_verifier",
        scorer_value_match="fail",
        label="false-positive",
    ),
    LabelRule(
        gesture="route-outstanding",
        scorer_id="sonnet_review_R4_tampering",
        scorer_value_match="yes-flagged",
        label="n/a",
    ),
    LabelRule(
        gesture="route-marker",
        scorer_id="sonnet_review_R4_tampering",
        scorer_value_match="yes-flagged",
        label="n/a",
    ),
    # `acknowledge` is non-judgmental — no rows.
)


def lookup_label(gesture: str, scorer_id: str, scorer_value: str) -> str | None:
    """Return the implicit ground-truth label for (gesture, scorer, value)
    or None if no rule matches.

    Caller passes the canonical scorer emission `score_value` (e.g.
    "yes-flagged" for R4, "fail" for Ralph). Returning None means the
    operator gesture does NOT imply a ground-truth label for this scorer
    emission.
    """
    for rule in IMPLICIT_LABELING_MATRIX:
        if (
            rule.gesture == gesture
            and rule.scorer_id == scorer_id
            and rule.scorer_value_match == scorer_value
        ):
            return rule.label
    return None


class UnregisteredScorerError(ValueError):
    """Raised when an emission attempts an unknown scorer_id."""


class InvalidEnumError(ValueError):
    """Raised when a closed-enum value is not in the spec."""


def validate_scorer_id(scorer_id: str) -> None:
    if scorer_id not in SCORER_IDS:
        raise UnregisteredScorerError(
            f"scorer_id={scorer_id!r} not in SCORER_IDS; "
            f"valid: {sorted(SCORER_IDS)}"
        )


def validate_score_category(category: str) -> None:
    if category not in SCORE_CATEGORIES:
        raise InvalidEnumError(
            f"score_category={category!r} not in {sorted(SCORE_CATEGORIES)}"
        )


def validate_ground_truth_label(label: str) -> None:
    if label not in GROUND_TRUTH_LABELS:
        raise InvalidEnumError(
            f"ground_truth_label={label!r} not in {sorted(GROUND_TRUTH_LABELS)}"
        )


def validate_ground_truth_source(source: str) -> None:
    if source not in GROUND_TRUTH_SOURCES:
        raise InvalidEnumError(
            f"ground_truth_source={source!r} not in {sorted(GROUND_TRUTH_SOURCES)}"
        )


__all__ = [
    "SCORER_IDS",
    "SCORE_CATEGORIES",
    "GROUND_TRUTH_LABELS",
    "GROUND_TRUTH_SOURCES",
    "SCORER_KIND",
    "LabelRule",
    "IMPLICIT_LABELING_MATRIX",
    "lookup_label",
    "UnregisteredScorerError",
    "InvalidEnumError",
    "validate_scorer_id",
    "validate_score_category",
    "validate_ground_truth_label",
    "validate_ground_truth_source",
]
