"""Strict-mode invariants beyond JSON Schema validation.

Per implplan §B.impl.9 line 1343:
    "--strict mode adds invariants beyond schema: task-id uniqueness within
     a document; depends_on references resolve to defined task IDs;
     plan_ref in orchestrator resolves to an existing <slug>_plan.json"
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from bin._render_plan.json_loader import SchemaRejectedError

PlanKind = Literal["plan", "orchestrator"]


def run_strict_invariants(
    payload: dict, kind: PlanKind, source_path: Path
) -> None:
    """Run cross-field invariants; raise `SchemaRejectedError` on failure.

    The caller (`bin/verify_plan` main) maps the exception to exit code
    4. We reuse the schema-rejection error class because the strict-mode
    failures are semantically "this document is malformed at the
    cross-field level" — the same disposition the chain driver applies
    to schema errors.
    """
    violations: list[dict] = []

    if kind == "plan":
        violations.extend(_check_task_skeleton_unique_ids(payload))
        violations.extend(_check_skeleton_depends_on_resolves(payload))
    else:
        violations.extend(_check_orch_task_unique_ids(payload))
        violations.extend(_check_orch_depends_on_resolves(payload))
        violations.extend(_check_plan_ref_exists(payload, source_path))
        violations.extend(_check_junctions_after_task_resolves(payload))

    if violations:
        raise SchemaRejectedError(
            path=str(source_path), violations=violations
        )


def _check_task_skeleton_unique_ids(payload: dict) -> list[dict]:
    seen: set[str] = set()
    dupes: list[str] = []
    for task in payload.get("tasks_skeleton", []) or []:
        tid = task.get("id")
        if tid in seen:
            dupes.append(tid)
        else:
            seen.add(tid)
    if not dupes:
        return []
    return [
        {
            "path": "/tasks_skeleton",
            "message": f"duplicate task ids in tasks_skeleton: {dupes}",
            "validator": "strict-unique-ids",
        }
    ]


def _check_skeleton_depends_on_resolves(payload: dict) -> list[dict]:
    ids: set[str] = {
        task["id"]
        for task in payload.get("tasks_skeleton", []) or []
        if "id" in task
    }
    unresolved: list[tuple[str, str]] = []
    for task in payload.get("tasks_skeleton", []) or []:
        for dep in task.get("depends_on", []) or []:
            if dep not in ids:
                unresolved.append((task.get("id", "<unknown>"), dep))
    if not unresolved:
        return []
    return [
        {
            "path": f"/tasks_skeleton[{task_id}]/depends_on",
            "message": f"depends_on '{dep}' does not resolve to a defined task id",
            "validator": "strict-depends-on-resolution",
        }
        for task_id, dep in unresolved
    ]


def _check_orch_task_unique_ids(payload: dict) -> list[dict]:
    seen: set[str] = set()
    dupes: list[str] = []
    for task in payload.get("tasks", []) or []:
        tid = task.get("id")
        if tid in seen:
            dupes.append(tid)
        else:
            seen.add(tid)
    if not dupes:
        return []
    return [
        {
            "path": "/tasks",
            "message": f"duplicate task ids in tasks: {dupes}",
            "validator": "strict-unique-ids",
        }
    ]


def _check_orch_depends_on_resolves(payload: dict) -> list[dict]:
    ids: set[str] = {
        task["id"] for task in payload.get("tasks", []) or [] if "id" in task
    }
    unresolved: list[tuple[str, str]] = []
    for task in payload.get("tasks", []) or []:
        for dep in task.get("depends_on", []) or []:
            if dep not in ids:
                unresolved.append((task.get("id", "<unknown>"), dep))
    if not unresolved:
        return []
    return [
        {
            "path": f"/tasks[{task_id}]/depends_on",
            "message": (
                f"depends_on '{dep}' does not resolve to a defined task id"
            ),
            "validator": "strict-depends-on-resolution",
        }
        for task_id, dep in unresolved
    ]


def _check_plan_ref_exists(payload: dict, source_path: Path) -> list[dict]:
    plan_ref = payload.get("plan_ref")
    if not plan_ref:
        return []
    # plan_ref is relative to the same directory as the orchestrator JSON.
    candidate = source_path.parent / plan_ref
    if candidate.exists():
        return []
    return [
        {
            "path": "/plan_ref",
            "message": (
                f"plan_ref '{plan_ref}' does not resolve to an existing file "
                f"(checked {candidate})"
            ),
            "validator": "strict-plan-ref-exists",
        }
    ]


def _check_junctions_after_task_resolves(payload: dict) -> list[dict]:
    task_ids: set[str] = {
        task["id"] for task in payload.get("tasks", []) or [] if "id" in task
    }
    unresolved: list[tuple[str, str]] = []
    for junction in payload.get("junctions", []) or []:
        after = junction.get("after_task")
        if after and after not in task_ids:
            unresolved.append((junction.get("id", "<unknown>"), after))
    if not unresolved:
        return []
    return [
        {
            "path": f"/junctions[{j_id}]/after_task",
            "message": (
                f"after_task '{after}' does not resolve to a defined task id"
            ),
            "validator": "strict-junction-resolution",
        }
        for j_id, after in unresolved
    ]


__all__ = ["run_strict_invariants"]
