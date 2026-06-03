"""Load `<slug>_orchestrator.json` into a plain dict (code_next_ready_pick T1).

Pure I/O surface; no schema validation beyond shape-of-tasks (full v1
schema validation happens upstream in render_plan/verify_plan). Raises
the closed-enum exception family that `main.py` maps to exit codes
10/11/12.

The picker only needs the orchestrator's `tasks` array to compute the
ready set; everything else (junctions, plan_ref, etc.) is opaque to the
library. Returning the full dict keeps the surface symmetric with
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
