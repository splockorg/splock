"""Sole writer of `docs/plans/<slug>/_regression_cases/<case_id>.json`.

Per splock implplan §J.impl.6. Promotion is the only path that
creates regression cases (operator-gestural via `bin/morning-review
mark-for-eval`). Auto-promotion is out-of-scope per §J.impl.13 #5.

Inline content discipline (load-bearing): `case_inputs` carries actual
content snapshots, not references. Replay-determinism across repo edits
is the load-bearing reason.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import re
from typing import Any, Optional

from bin._render_plan.atomic_write import write_atomic
from . import failure_capture


EXPECTED_OUTCOMES: frozenset[str] = frozenset(
    {
        "scorer_should_flag",
        "scorer_should_pass",
        "system_should_halt",
        "system_should_proceed",
        "hook_should_refuse",
        "hook_should_allow",
    }
)

CASE_STATUS: frozenset[str] = frozenset({"active", "retired"})


class InvalidExpectedOutcomeError(ValueError):
    pass


class InvalidCaseStatusError(ValueError):
    pass


class PromotionIdempotentNoop(RuntimeError):
    """Sentinel: case_id already exists for the same source_failure_id_ref."""


class CaseNotFoundError(LookupError):
    pass


class CaseAlreadyRetiredError(RuntimeError):
    pass


def _now_iso_z() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _cases_dir(plan_dir: pathlib.Path) -> pathlib.Path:
    return plan_dir / "_regression_cases"


def _next_seq(plan_dir: pathlib.Path, label_token: str) -> int:
    """Scan existing case files for `case_<label_token>_<seq>` and return
    the next seq (max + 1, starting at 1).
    """
    cdir = _cases_dir(plan_dir)
    if not cdir.exists():
        return 1
    pattern = re.compile(
        r"^case_" + re.escape(label_token) + r"_(\d+)\.json$"
    )
    highest = 0
    for p in cdir.iterdir():
        m = pattern.match(p.name)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


def _sanitize_label(label: str) -> str:
    # Operator-supplied label sanitized to alnum + _ + -.
    clean = re.sub(r"[^A-Za-z0-9_-]", "_", label).strip("_")
    return clean or "case"


def _materialize_inline(
    plan_dir: pathlib.Path,
    failure_payload: dict,
) -> dict[str, Any]:
    """Resolve `context_inputs` references into inline content snapshots.

    For each (key, value) in `context_inputs`, attempt to read the path
    portion (ignoring `@<sha>` suffix). When the file does not exist or
    the @sha cannot be resolved, fall back to a stub
    `{"unresolved": <ref>}`. The full git-show resolution is operator-
    grade and replay-determinism only kicks in when the file exists.
    """
    repo_root = _repo_root()
    inputs = failure_payload.get("context_inputs", {})
    outputs = failure_payload.get("context_outputs", {})
    inlined: dict[str, Any] = {}
    for key, ref in {**inputs, **outputs}.items():
        if not isinstance(ref, str):
            inlined[key] = ref
            continue
        path_part = ref.split("@", 1)[0]
        candidate = repo_root / path_part
        if candidate.exists() and candidate.is_file():
            try:
                inlined[key + "_content"] = candidate.read_text(encoding="utf-8")
            except OSError:
                inlined[key + "_content"] = {"unresolved": ref}
        else:
            inlined[key + "_content"] = {"unresolved": ref}
    return inlined


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def promote(
    plan_dir: pathlib.Path,
    *,
    failure_id: str,
    expected_outcome: str,
    expected_outcome_details: dict,
    labels: list[str],
    case_id: Optional[str] = None,
    operator_correction: Optional[str] = None,
    reactivate_retired: bool = False,
) -> str:
    """Promote a captured failure to a regression case.

    Returns the new `case_id`. Raises `PromotionIdempotentNoop` if a case
    for the same `source_failure_id_ref` already exists. With
    `reactivate_retired=True`, an existing retired case may be reactivated
    without duplicate.
    """
    if expected_outcome not in EXPECTED_OUTCOMES:
        raise InvalidExpectedOutcomeError(
            f"expected_outcome={expected_outcome!r} not in {sorted(EXPECTED_OUTCOMES)}"
        )

    failure = failure_capture.read_failure(plan_dir, failure_id)
    if failure is None:
        raise CaseNotFoundError(
            f"no _failures/{failure_id}.json under {plan_dir}"
        )

    cdir = _cases_dir(plan_dir)
    cdir.mkdir(parents=True, exist_ok=True)

    # Check existing cases for same source_failure_id_ref.
    for p in cdir.glob("case_*.json"):
        try:
            existing = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if existing.get("source_failure_id_ref") == failure_id:
            if reactivate_retired and existing.get("case_status") == "retired":
                existing["case_status"] = "active"
                body = json.dumps(existing, indent=2, sort_keys=True, ensure_ascii=False)
                write_atomic(p, body + "\n")
                return existing["case_id"]
            raise PromotionIdempotentNoop(
                f"failure_id={failure_id!r} already promoted to "
                f"case_id={existing.get('case_id')!r}"
            )

    # Derive case_id.
    if case_id is None:
        token = _sanitize_label(failure_id)
        seq = _next_seq(plan_dir, token)
        case_id = f"case_{token}_{seq}"
    else:
        target_check = cdir / f"{case_id}.json"
        if target_check.exists():
            raise PromotionIdempotentNoop(
                f"case_id={case_id!r} collision (file already exists)"
            )

    row: dict[str, Any] = {
        "schema_version": 1,
        "case_id": case_id,
        "created_at": _now_iso_z(),
        "created_by": "operator",
        "source_failure_id_ref": failure_id,
        "case_inputs": _materialize_inline(plan_dir, failure),
        "expected_outcome": expected_outcome,
        "expected_outcome_details": expected_outcome_details,
        "operator_correction": operator_correction,
        "labels": list(labels),
        "case_status": "active",
    }
    target = cdir / f"{case_id}.json"
    body = json.dumps(row, indent=2, sort_keys=True, ensure_ascii=False)
    write_atomic(target, body + "\n")

    # Update source failure with promotion pointer.
    failure_capture.mark_promoted(plan_dir, failure_id, case_id)
    return case_id


def retire(plan_dir: pathlib.Path, case_id: str, reason: str) -> None:
    """Move a case to retired status. Raises CaseNotFoundError or
    CaseAlreadyRetiredError.
    """
    if not reason:
        raise ValueError("retire requires non-empty reason")
    target = _cases_dir(plan_dir) / f"{case_id}.json"
    if not target.exists():
        raise CaseNotFoundError(case_id)
    payload = json.loads(target.read_text(encoding="utf-8"))
    if payload.get("case_status") == "retired":
        raise CaseAlreadyRetiredError(case_id)
    payload["case_status"] = "retired"
    payload.setdefault("operator_correction", None)
    # Persist retirement reason in operator_correction (multi-use field).
    existing = payload.get("operator_correction") or ""
    payload["operator_correction"] = (
        existing + ("\n" if existing else "") + f"RETIRED: {reason}"
    )
    body = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    write_atomic(target, body + "\n")


def read_case(plan_dir: pathlib.Path, case_id: str) -> Optional[dict]:
    target = _cases_dir(plan_dir) / f"{case_id}.json"
    if not target.exists():
        return None
    return json.loads(target.read_text(encoding="utf-8"))


def list_cases(
    plan_dir: pathlib.Path,
    *,
    include_retired: bool = False,
) -> list[dict]:
    cdir = _cases_dir(plan_dir)
    if not cdir.exists():
        return []
    out: list[dict] = []
    for p in sorted(cdir.glob("case_*.json")):
        try:
            row = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not include_retired and row.get("case_status") == "retired":
            continue
        out.append(row)
    return out


__all__ = [
    "EXPECTED_OUTCOMES",
    "CASE_STATUS",
    "InvalidExpectedOutcomeError",
    "InvalidCaseStatusError",
    "PromotionIdempotentNoop",
    "CaseNotFoundError",
    "CaseAlreadyRetiredError",
    "promote",
    "retire",
    "read_case",
    "list_cases",
]
