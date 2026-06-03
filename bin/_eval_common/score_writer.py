"""Sole writer of `docs/plans/<slug>/_scores.jsonl`.

Per splock implplan §J.impl.4. Two row types (`emission` and
`label`) share one append-only JSONL file. Concurrent writers (Ralph +
Sonnet + test runner + chain driver) serialize via per-file flock.

Operation sequence mirrors §C.impl.5:
1. Pre-flock closed-enum rejection (UnregisteredScorerError / InvalidEnumError).
2. Stamp writer-supplied fields.
3. Acquire flock on `<plan_dir>/_scores.jsonl.lock`.
4. Post-flock schema validation.
5. Write JSONL `ab` mode + flush + fsync.
6. Release flock implicit on context exit.

Forward-compat per §B.impl.6: unknown `schema_version` refuses with exit
code 5 + structured stderr (consumer-side, in CLI dispatchers).
"""

from __future__ import annotations

import contextlib
import datetime
import fcntl
import json
import os
import pathlib
import secrets
from typing import Any, Iterator, Optional

from .scorer_registry import (
    GROUND_TRUTH_LABELS,
    GROUND_TRUTH_SOURCES,
    SCORE_CATEGORIES,
    SCORER_IDS,
    UnregisteredScorerError,
    InvalidEnumError,
    validate_ground_truth_label,
    validate_ground_truth_source,
    validate_score_category,
    validate_scorer_id,
)


SCORES_BASENAME = "_scores.jsonl"
LOCK_SUFFIX = ".lock"

SUPPORTED_VERSIONS_SCORES: list[int] = [1, 2]
CURRENT_SCHEMA_VERSION: int = 2


def _now_iso_z() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def scores_path(plan_dir: pathlib.Path) -> pathlib.Path:
    return plan_dir / SCORES_BASENAME


def lockfile_path(plan_dir: pathlib.Path) -> pathlib.Path:
    return plan_dir / (SCORES_BASENAME + LOCK_SUFFIX)


@contextlib.contextmanager
def _acquire_exclusive(plan_dir: pathlib.Path) -> Iterator[int]:
    plan_dir.mkdir(parents=True, exist_ok=True)
    lp = lockfile_path(plan_dir)
    fd = os.open(lp, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield fd
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _new_score_id() -> str:
    """Return `score_<8hex>` per spec."""
    return "score_" + secrets.token_hex(4)


def _append_one_row(target: pathlib.Path, row: dict) -> None:
    payload = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    with target.open("ab") as fh:
        fh.write(payload.encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())


# --- Minimal schema-shape validation post-stamp -------------------------


def _validate_emission_row(row: dict) -> None:
    required = (
        "schema_version",
        "row_type",
        "score_id",
        "ts",
        "scorer_id",
        "trace_id",
        "session_id",
        "score_value",
        "score_category",
    )
    for k in required:
        if k not in row:
            raise InvalidEnumError(f"emission row missing required field: {k}")
    if row["schema_version"] not in SUPPORTED_VERSIONS_SCORES:
        raise InvalidEnumError(
            f"emission schema_version={row['schema_version']!r} unsupported"
        )
    if row["row_type"] != "emission":
        raise InvalidEnumError(f"row_type={row['row_type']!r} must be 'emission'")
    if row["scorer_id"] not in SCORER_IDS:
        raise UnregisteredScorerError(f"scorer_id={row['scorer_id']!r}")
    if row["score_category"] not in SCORE_CATEGORIES:
        raise InvalidEnumError(f"score_category={row['score_category']!r}")


def _validate_label_row(row: dict) -> None:
    required = (
        "schema_version",
        "row_type",
        "score_id_ref",
        "label_ts",
        "ground_truth_label",
        "ground_truth_source",
    )
    for k in required:
        if k not in row:
            raise InvalidEnumError(f"label row missing required field: {k}")
    if row["schema_version"] not in SUPPORTED_VERSIONS_SCORES:
        raise InvalidEnumError(
            f"label schema_version={row['schema_version']!r} unsupported"
        )
    if row["row_type"] != "label":
        raise InvalidEnumError(f"row_type={row['row_type']!r} must be 'label'")
    if row["ground_truth_label"] not in GROUND_TRUTH_LABELS:
        raise InvalidEnumError(
            f"ground_truth_label={row['ground_truth_label']!r}"
        )
    if row["ground_truth_source"] not in GROUND_TRUTH_SOURCES:
        raise InvalidEnumError(
            f"ground_truth_source={row['ground_truth_source']!r}"
        )


# --- Public writers -----------------------------------------------------


def append_emission(
    plan_dir: pathlib.Path,
    *,
    scorer_id: str,
    trace_id: str,
    session_id: str,
    score_value: Any,
    score_category: str,
    task_id: Optional[str] = None,
    scorer_attributes: Optional[dict] = None,
) -> str:
    """Append an emission row; return the new `score_id`.

    Pre-flock validation rejects unknown scorer_id or score_category. The
    `score_id` is generated server-side and returned for caller-side
    correlation (e.g. to attach a label later).
    """
    # Step 1 — pre-flock validation.
    validate_scorer_id(scorer_id)
    validate_score_category(score_category)

    # Step 2 — stamp.
    score_id = _new_score_id()
    row: dict = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "row_type": "emission",
        "score_id": score_id,
        "ts": _now_iso_z(),
        "scorer_id": scorer_id,
        "trace_id": trace_id,
        "session_id": session_id,
        "score_value": score_value,
        "score_category": score_category,
    }
    if task_id is not None:
        row["task_id"] = task_id
    if scorer_attributes is not None:
        row["scorer_attributes"] = scorer_attributes

    # Step 3 — flock + Step 4-5 — validate + write under lock.
    target = scores_path(plan_dir)
    with _acquire_exclusive(plan_dir):
        _validate_emission_row(row)
        _append_one_row(target, row)
    return score_id


def append_label(
    plan_dir: pathlib.Path,
    *,
    score_id_ref: str,
    ground_truth_label: str,
    ground_truth_source: str,
    operator_notes: Optional[str] = None,
) -> None:
    """Append a label row referencing an existing emission's `score_id`.

    Pre-flock validation rejects unknown enum values. Double-labelling
    detection (raising on a pre-existing label for the same
    `score_id_ref`) is the caller's responsibility — typically
    `bin/morning-review label-score` checks before invoking.
    """
    validate_ground_truth_label(ground_truth_label)
    validate_ground_truth_source(ground_truth_source)

    row: dict = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "row_type": "label",
        "score_id_ref": score_id_ref,
        "label_ts": _now_iso_z(),
        "ground_truth_label": ground_truth_label,
        "ground_truth_source": ground_truth_source,
    }
    if operator_notes is not None:
        row["operator_notes"] = operator_notes

    target = scores_path(plan_dir)
    with _acquire_exclusive(plan_dir):
        _validate_label_row(row)
        _append_one_row(target, row)


# --- Reader ----------------------------------------------------------------


def iter_rows(plan_dir: pathlib.Path) -> Iterator[dict]:
    """Yield each row in `_scores.jsonl`. Forward-compat refusal on
    unknown schema_version raises `InvalidEnumError`.

    Corrupt rows are skipped silently (recovery-aware reader semantics).
    """
    target = scores_path(plan_dir)
    if not target.exists():
        return
    with target.open("rb") as fh:
        for raw in fh:
            stripped = raw.rstrip(b"\n")
            if not stripped:
                continue
            try:
                row = json.loads(stripped.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(row, dict):
                continue
            sv = row.get("schema_version")
            if sv not in SUPPORTED_VERSIONS_SCORES:
                # forward-compat refusal — caller's CLI maps to exit 5
                raise InvalidEnumError(
                    f"unsupported_schema_version: kind=scores seen={sv!r} "
                    f"supported={SUPPORTED_VERSIONS_SCORES}"
                )
            yield row


def find_emission(plan_dir: pathlib.Path, score_id: str) -> Optional[dict]:
    """Return the emission row with the given `score_id`, or None."""
    for row in iter_rows(plan_dir):
        if row.get("row_type") == "emission" and row.get("score_id") == score_id:
            return row
    return None


def has_label_for(plan_dir: pathlib.Path, score_id_ref: str) -> bool:
    """Return True if any label row references `score_id_ref`."""
    for row in iter_rows(plan_dir):
        if (
            row.get("row_type") == "label"
            and row.get("score_id_ref") == score_id_ref
        ):
            return True
    return False


__all__ = [
    "append_emission",
    "append_label",
    "iter_rows",
    "find_emission",
    "has_label_for",
    "scores_path",
    "lockfile_path",
    "SUPPORTED_VERSIONS_SCORES",
    "CURRENT_SCHEMA_VERSION",
]
