"""Load + normalize `_state.json` shape (code_next_ready_pick T1 V2/D-shape).

Per T0 V2 branch decision (`code_next_ready_pick_plan.md`): the on-disk
canonical shape today is **dict-keyed-by-id** (per
`bin/_update_orchestrator/state_writer.py:93`); the v1 schema declares
**array-of-objects-with-id**. Both shapes exist in the wild (all 4 live
`_state.json` files are dict-shaped as of 2026-05-23). The picker MUST
accept either and produce a single internal canonical form keyed by id;
only genuinely malformed shapes (`tasks` neither dict nor array) trigger
the `EXIT_STATE_SHAPE_INVALID=13` refusal.

Absence semantics mirror `bin._update_orchestrator.state_writer.read_state`:
missing `_state.json` returns an empty state (`{"tasks": {}}` canonical
form).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StateShapeInvalidError(Exception):
    """`_state.json` parsed, but `tasks` is neither dict nor array."""


def state_path(plan_dir: Path) -> Path:
    return Path(plan_dir) / "_state.json"


def load_state(plan_dir: Path) -> dict:
    """Read + normalize `_state.json`; return state with `tasks` as dict-by-id.

    Returns
    -------
    dict
        The canonical-form state with `tasks` always shaped as
        `dict[str, dict]` keyed by task id. Other top-level keys
        (schema_version, slug, phase) pass through unchanged.

    Raises
    ------
    StateShapeInvalidError
        - JSON parse failure on a present file
        - `tasks` field present but neither dict nor array

    Notes
    -----
    Missing file returns `{"tasks": {}}` (canonical empty state) —
    mirrors `bin/_update_orchestrator/state_writer.read_state` which
    returns `{}`, but normalized so downstream `state.get("tasks")` is
    always a dict.
    """
    path = state_path(plan_dir)
    if not path.exists():
        return {"tasks": {}}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateShapeInvalidError(
            f"parse failure at {path}: {exc.msg} (line {exc.lineno}, col {exc.colno})"
        ) from exc
    except OSError as exc:
        raise StateShapeInvalidError(
            f"read failure at {path}: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise StateShapeInvalidError(
            f"top-level payload at {path} is {type(payload).__name__}, expected object"
        )

    return _normalize(payload, source=str(path))


def _normalize(state: dict, *, source: str) -> dict:
    """Return state with `tasks` as `dict[id, entry]`.

    Accepts:
    - dict-form (legacy / state_writer current emission): pass through.
    - array-form (v1 schema-conformant): convert to dict-by-id.
    - missing key: insert empty dict.

    Raises `StateShapeInvalidError` for any other shape.
    """
    out: dict[str, Any] = dict(state)  # shallow copy; we mutate `tasks`
    tasks = out.get("tasks")

    if tasks is None:
        out["tasks"] = {}
        return out

    if isinstance(tasks, dict):
        # Already canonical; pass through but ensure entries are dicts so
        # downstream code can `.get("status")` safely. Non-dict entries
        # pass through; the picker's status-resolution treats them as
        # default-`ready` (consistent with state_writer.get_task_status).
        out["tasks"] = dict(tasks)
        return out

    if isinstance(tasks, list):
        rebuilt: dict[str, dict] = {}
        for idx, entry in enumerate(tasks):
            if not isinstance(entry, dict):
                raise StateShapeInvalidError(
                    f"`tasks[{idx}]` at {source} is "
                    f"{type(entry).__name__}, expected object"
                )
            task_id = entry.get("id")
            if not isinstance(task_id, str) or not task_id:
                raise StateShapeInvalidError(
                    f"`tasks[{idx}]` at {source} is missing required string `id` field"
                )
            # Copy the entry sans `id` (id becomes the dict key); preserve
            # all other fields verbatim so status + extras survive.
            cleaned = {k: v for k, v in entry.items() if k != "id"}
            rebuilt[task_id] = cleaned
        out["tasks"] = rebuilt
        return out

    raise StateShapeInvalidError(
        f"`tasks` at {source} is {type(tasks).__name__}, expected dict or array"
    )
