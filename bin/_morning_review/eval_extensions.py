"""§J.impl.8 morning-review extensions: mark-for-eval / label-score / retire-case.

Wired into `bin/_morning_review/main.py` v1.4 stubs at §J.impl ship.
Sub-emitter constants live in `log_emit.py` (EMIT_MARK_FOR_EVAL etc.).
"""

from __future__ import annotations

import pathlib
import sys
from typing import Optional

from bin._eval_common import failure_capture, regression_case, score_writer

from . import log_emit
from .exit_codes import (
    EXIT_ATOMIC_WRITE_FAILED,
    EXIT_OK,
    EXIT_PROMOTION_IDEMPOTENT_NOOP,
    EXIT_QUEUE_ENTRY_NOT_FOUND,
    EXIT_TRIAGE_DOUBLE_CLOSE,
    EXIT_USAGE,
)


def _resolve_plan_dir_for_failure(
    repo_root: pathlib.Path, failure_id: str
) -> Optional[pathlib.Path]:
    """Locate the plan_dir containing `_failures/<failure_id>.json`.

    Searches `docs/plans/*/`. Returns the first match (or None).
    """
    plans_root = repo_root / "docs" / "plans"
    if not plans_root.exists():
        return None
    for plan_dir in plans_root.iterdir():
        if not plan_dir.is_dir():
            continue
        candidate = plan_dir / "_failures" / f"{failure_id}.json"
        if candidate.exists():
            return plan_dir
    return None


def _resolve_plan_dir_for_score(
    repo_root: pathlib.Path, score_id: str
) -> Optional[tuple[pathlib.Path, dict]]:
    """Locate the plan_dir containing an emission row with `score_id`."""
    plans_root = repo_root / "docs" / "plans"
    if not plans_root.exists():
        return None
    for plan_dir in plans_root.iterdir():
        if not plan_dir.is_dir():
            continue
        emission = score_writer.find_emission(plan_dir, score_id)
        if emission is not None:
            return (plan_dir, emission)
    return None


def _resolve_plan_dir_for_case(
    repo_root: pathlib.Path, case_id: str
) -> Optional[pathlib.Path]:
    plans_root = repo_root / "docs" / "plans"
    if not plans_root.exists():
        return None
    for plan_dir in plans_root.iterdir():
        if not plan_dir.is_dir():
            continue
        candidate = plan_dir / "_regression_cases" / f"{case_id}.json"
        if candidate.exists():
            return plan_dir
    return None


def mark_for_eval(
    *,
    repo_root: pathlib.Path,
    failure_id: str,
    labels: Optional[str] = None,
    case_id: Optional[str] = None,
    reactivate_retired: bool = False,
    dry_run: bool = False,
    json_output: bool = False,
    expected_outcome: str = "system_should_halt",
    expected_outcome_details: Optional[dict] = None,
) -> int:
    """Promote a captured failure to a regression case.

    Note: in interactive use, expected_outcome + details would be prompted
    from the operator; v2.7 ships defaults (`system_should_halt`) suitable
    for the most common "we want this halt to be detected" promotion. The
    operator can override via downstream CLI args (future expansion).
    """
    plan_dir = _resolve_plan_dir_for_failure(repo_root, failure_id)
    if plan_dir is None:
        print(
            f"failure_id not found: {failure_id}", file=sys.stderr
        )
        return EXIT_QUEUE_ENTRY_NOT_FOUND

    labels_list = [s.strip() for s in (labels or "").split(",") if s.strip()]

    if dry_run:
        if json_output:
            import json as _json

            print(
                _json.dumps(
                    {
                        "dry_run": True,
                        "failure_id": failure_id,
                        "plan_dir": str(plan_dir.relative_to(repo_root)),
                        "labels": labels_list,
                    }
                )
            )
        else:
            print(
                f"[dry-run] would promote {failure_id} → regression case "
                f"in {plan_dir.relative_to(repo_root)}"
            )
        return EXIT_OK

    try:
        new_case_id = regression_case.promote(
            plan_dir,
            failure_id=failure_id,
            expected_outcome=expected_outcome,
            expected_outcome_details=expected_outcome_details or {},
            labels=labels_list,
            case_id=case_id,
            reactivate_retired=reactivate_retired,
        )
    except regression_case.PromotionIdempotentNoop as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_PROMOTION_IDEMPOTENT_NOOP
    except OSError as exc:
        print(f"atomic_write_failed: {exc}", file=sys.stderr)
        return EXIT_ATOMIC_WRITE_FAILED

    # Emit acknowledgement row.
    try:
        log_emit.emit_triage(
            plan_dir,
            slug=plan_dir.name,
            task_id=failure_id,
            event_type="regression_case_promoted",
            sub_emitter=log_emit.EMIT_MARK_FOR_EVAL,
            reason=f"promoted {failure_id} → {new_case_id}",
        )
    except Exception:
        pass

    if json_output:
        import json as _json

        print(_json.dumps({"failure_id": failure_id, "case_id": new_case_id}))
    else:
        print(f"promoted {failure_id} → {new_case_id}")
    return EXIT_OK


def label_score(
    *,
    repo_root: pathlib.Path,
    score_id: str,
    label: str,
    source: str = "operator_morning_review",
    notes: Optional[str] = None,
    dry_run: bool = False,
    json_output: bool = False,
) -> int:
    """Attach a ground-truth label to a score emission."""
    found = _resolve_plan_dir_for_score(repo_root, score_id)
    if found is None:
        print(f"score_id not found: {score_id}", file=sys.stderr)
        return EXIT_QUEUE_ENTRY_NOT_FOUND
    plan_dir, _ = found
    if score_writer.has_label_for(plan_dir, score_id):
        print(f"score already labeled: {score_id}", file=sys.stderr)
        return EXIT_TRIAGE_DOUBLE_CLOSE

    if dry_run:
        if json_output:
            import json as _json

            print(
                _json.dumps(
                    {
                        "dry_run": True,
                        "score_id": score_id,
                        "label": label,
                        "source": source,
                    }
                )
            )
        else:
            print(f"[dry-run] would label {score_id} as {label}")
        return EXIT_OK

    try:
        score_writer.append_label(
            plan_dir,
            score_id_ref=score_id,
            ground_truth_label=label,
            ground_truth_source=source,
            operator_notes=notes,
        )
    except OSError as exc:
        print(f"atomic_write_failed: {exc}", file=sys.stderr)
        return EXIT_ATOMIC_WRITE_FAILED

    try:
        log_emit.emit_triage(
            plan_dir,
            slug=plan_dir.name,
            task_id=None,
            event_type="score_labeled",
            sub_emitter=log_emit.EMIT_LABEL_SCORE,
            reason=f"labeled {score_id} as {label}",
        )
    except Exception:
        pass

    if json_output:
        import json as _json

        print(_json.dumps({"score_id": score_id, "label": label}))
    else:
        print(f"labeled {score_id} as {label}")
    return EXIT_OK


def retire_case(
    *,
    repo_root: pathlib.Path,
    case_id: str,
    reason: str,
    dry_run: bool = False,
    json_output: bool = False,
) -> int:
    if not reason:
        print("retire-case requires --reason", file=sys.stderr)
        return EXIT_USAGE
    plan_dir = _resolve_plan_dir_for_case(repo_root, case_id)
    if plan_dir is None:
        print(f"case_id not found: {case_id}", file=sys.stderr)
        return EXIT_QUEUE_ENTRY_NOT_FOUND

    if dry_run:
        if json_output:
            import json as _json

            print(
                _json.dumps(
                    {"dry_run": True, "case_id": case_id, "reason": reason}
                )
            )
        else:
            print(f"[dry-run] would retire {case_id}: {reason}")
        return EXIT_OK

    try:
        regression_case.retire(plan_dir, case_id, reason)
    except regression_case.CaseAlreadyRetiredError:
        print(f"case already retired: {case_id}", file=sys.stderr)
        return EXIT_TRIAGE_DOUBLE_CLOSE
    except OSError as exc:
        print(f"atomic_write_failed: {exc}", file=sys.stderr)
        return EXIT_ATOMIC_WRITE_FAILED

    try:
        log_emit.emit_triage(
            plan_dir,
            slug=plan_dir.name,
            task_id=case_id,
            event_type="regression_case_retired",
            sub_emitter=log_emit.EMIT_RETIRE_CASE,
            reason=f"retired {case_id}: {reason}",
        )
    except Exception:
        pass

    if json_output:
        import json as _json

        print(_json.dumps({"case_id": case_id, "retired": True}))
    else:
        print(f"retired {case_id}")
    return EXIT_OK


__all__ = ["mark_for_eval", "label_score", "retire_case"]
