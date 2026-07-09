"""Load `<slug>_orchestrator.json` into a plain dict (code_next_ready_pick T1).

Pure I/O surface; no schema validation beyond shape-of-tasks (full v1
schema validation happens upstream in render_plan/verify_plan). Raises
the closed-enum exception family that `main.py` maps to exit codes
10/11/12.

The picker only needs the orchestrator's `tasks` array to compute the
ready set. Junctions are no longer fully opaque to the library: since
real_tests_at_junctions T4 (SC6) this module also owns
`junction_covering_set`, the junction -> task binding helper that
resolves which tasks' `tests_enabled` a junction test_gate
consolidates (explicit `covers[]` verbatim, else the documented
default). Everything else (plan_ref, etc.) remains opaque. Returning
the full dict keeps the surface symmetric with
`state_loader.load_state` and leaves room for future fields without
re-touching this module.
"""

from __future__ import annotations

import json
from pathlib import Path


class SlugNotFoundError(Exception):
    """`docs/plans/<slug>/` does not exist; slug typo or never-planned."""


class OrchestratorJsonMissingError(Exception):
    """Plan dir exists but `<slug>_orchestrator.json` is missing."""


class OrchestratorJsonMalformedError(Exception):
    """JSON parse failure OR `tasks` field missing / non-array.

    Full schema validation is the chain driver's job; this module only
    rejects shapes that would crash the picker's downstream computation
    (i.e., `tasks` not present or not a list).
    """


def orchestrator_path(plan_dir: Path, slug: str) -> Path:
    return plan_dir / f"{slug}_orchestrator.json"


def load_orchestrator(plan_dir: Path, slug: str) -> dict:
    """Load + lightly validate `<slug>_orchestrator.json`.

    Returns
    -------
    dict
        The parsed orchestrator payload. The caller (compute_ready_set)
        reads `orch["tasks"]` and indexes into each task's `id`,
        `depends_on`, etc.

    Raises
    ------
    SlugNotFoundError
        `plan_dir` itself does not exist.
    OrchestratorJsonMissingError
        `plan_dir` exists but `<slug>_orchestrator.json` is missing.
    OrchestratorJsonMalformedError
        JSON parse failure OR `tasks` field missing / non-array.
    """
    plan_dir = Path(plan_dir)
    if not plan_dir.exists():
        raise SlugNotFoundError(f"plan dir not found: {plan_dir}")

    path = orchestrator_path(plan_dir, slug)
    if not path.exists():
        raise OrchestratorJsonMissingError(
            f"orchestrator JSON not found: {path}"
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OrchestratorJsonMalformedError(
            f"parse failure at {path}: {exc.msg} (line {exc.lineno}, col {exc.colno})"
        ) from exc
    except OSError as exc:
        raise OrchestratorJsonMalformedError(
            f"read failure at {path}: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise OrchestratorJsonMalformedError(
            f"top-level payload at {path} is {type(payload).__name__}, expected object"
        )

    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        raise OrchestratorJsonMalformedError(
            f"`tasks` field at {path} is "
            f"{type(tasks).__name__ if tasks is not None else 'missing'}, expected array"
        )

    return payload


def junction_covering_set(orchestrator: dict, junction: dict) -> list[str]:
    """Resolve which tasks' `tests_enabled` a junction consolidates (SC6).

    The junction -> task binding contract (real_tests_at_junctions T4):

    - Explicit ``covers[]`` wins verbatim (order preserved, no dedupe).
      Every entry must reference an existing task id; a bogus entry
      raises ``ValueError`` naming the junction and the bad id. An
      explicitly-empty ``covers: []`` also raises — the schema rejects
      it (``minItems: 1``) so a vacuous test_gate is never expressible;
      omit the field to get the default.
    - When ``covers[]`` is absent, the default is the plan's SC6 rule:
      "a documented default of all prior tasks through after_task" —
      i.e. the tasks-array-order prefix up to AND INCLUDING the task
      whose id equals ``after_task``. An ``after_task`` that does not
      resolve to a task id raises ``ValueError`` (upstream,
      `bin/_verify_plan/strict.py::_check_junctions_after_task_resolves`
      rejects that orchestrator at plan time).

    This is the seam the junction-time collect-only oracle imports to
    build the consolidated covering set before allowing advance: a
    selector belonging to a task OUTSIDE this set must not satisfy the
    gate.

    Parameters
    ----------
    orchestrator:
        Parsed orchestrator payload (e.g. from `load_orchestrator`);
        only `tasks` is read.
    junction:
        One entry of the orchestrator's `junctions` array.

    Returns
    -------
    list[str]
        Task ids whose `tests_enabled` the junction consolidates.
    """
    j_id = junction.get("id", "<unknown>")
    tasks = orchestrator.get("tasks", []) or []
    task_ids = [task["id"] for task in tasks if "id" in task]

    covers = junction.get("covers")
    if covers is not None:
        if not covers:
            raise ValueError(
                f"junction '{j_id}': explicit covers[] is empty — a vacuous "
                f"covering set is schema-rejected (minItems 1); omit covers "
                f"to get the default (all prior tasks through after_task)"
            )
        known = set(task_ids)
        bogus = [tid for tid in covers if tid not in known]
        if bogus:
            raise ValueError(
                f"junction '{j_id}': covers[] references unknown task id(s) "
                f"{bogus} — every entry must be an existing task id"
            )
        return list(covers)

    after = junction.get("after_task")
    if after not in task_ids:
        raise ValueError(
            f"junction '{j_id}': after_task '{after}' does not resolve to a "
            f"defined task id; cannot compute the default covering set"
        )
    return task_ids[: task_ids.index(after) + 1]
