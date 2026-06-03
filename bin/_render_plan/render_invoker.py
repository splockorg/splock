"""In-process render invocation for `bin/update_orchestrator` wiring (orch_status_render T4).

Per the orch_status_render Phase 3 design: every `_state.json` mutation
performed by `bin/update_orchestrator` must produce a matching canonical
`_orchestrator.md` render. The driver wiring lives in
`bin._update_orchestrator.main._dispatch_base` and
`._dispatch_from_develop_plan`; this module is the seam those dispatch
paths call into.

Design choices:

1.  **No subprocess.** The wiring runs in-process so we share the flock
    held by `state_flock(plan_dir)`. Spawning `bin/render_plan` as a
    subprocess would block on the same lock (deadlock) or require the
    subprocess to skip flock acquisition (defeating the rest of the
    safety net).

2.  **`assume_flock_held` flag.** When called from inside an existing
    `state_flock` context (the dispatch paths), `assume_flock_held=True`
    skips re-acquisition. When called standalone (e.g., a future
    operator CLI or a pre-commit hook), `assume_flock_held=False`
    acquires the flock itself.

3.  **Composes existing public surfaces.** Uses `load_plan_json`,
    `validate_against_schema`, `read_existing_md`, `extract_anchor_content`,
    `render_canonical_body`, and `write_atomic` — every byte the rendered
    MD emits comes from the same code path as the standalone
    `bin/render_plan` CLI. Only the orchestration (no argparse, no exit
    codes; uses exceptions) differs.

4.  **Dict-form / list-form bridging.** The legacy `state_writer`
    maintains `tasks` as `dict[id, entry]`; the v1 schema expects
    `list[entry]` per v2.7 §E.2. We adapt dict-form to list-form *only*
    when the in-file shape is dict (preserving legacy behavior and
    schema compliance simultaneously). This adaptation is transparent
    to the caller — the `_state.json` on disk is untouched.

Exceptions propagated to the caller (the dispatch path):

- `PlanNotFoundError` — `_state.json` absent. Should be impossible if
  the caller just wrote it; surfaced as a generic render failure.
- `JsonMalformedError` — `_state.json` parse failure.
- `SchemaRejectedError` — `_state.json` shape mismatch against
  `_state_v1.schema.json`.
- `UnsupportedSchemaVersion` — `schema_version` outside the supported
  set.
- `TemplateError` — render template loading or substitution failure.
- `AtomicWriteError` — `_orchestrator.md` temp-then-rename failure.

The dispatch path catches all of these and emits
`state_md_render_failed` + propagates a non-zero exit code drawn from
`bin._render_plan.exit_codes`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Re-export shared exception types so callers can import from one place.
from .atomic_write import AtomicWriteError, write_atomic
from .human_notes import extract_anchor_content
from .json_loader import (
    JsonMalformedError,
    PlanNotFoundError,
    SchemaRejectedError,
    UnsupportedSchemaVersion,
    load_plan_json,
    validate_against_schema,
)
from .md_parser import read_existing_md
from .md_renderer import TemplateError, render_canonical_body


__all__ = [
    "render_state_under_flock",
    # Exception passthroughs for caller convenience:
    "PlanNotFoundError",
    "JsonMalformedError",
    "SchemaRejectedError",
    "UnsupportedSchemaVersion",
    "TemplateError",
    "AtomicWriteError",
]


def render_state_under_flock(
    plan_dir: Path,
    *,
    assume_flock_held: bool,
) -> None:
    """Render `_orchestrator.md` from `_state.json` for the given plan dir.

    Parameters
    ----------
    plan_dir : Path
        Directory containing `_state.json` (input) and `_orchestrator.md`
        (output). Both filenames are no-slug per v2.7 §5.B / §E.2.
    assume_flock_held : bool
        When True (the dispatch-path case), the caller already holds
        `state_flock(plan_dir)`; skip the inner acquisition. When False
        (standalone invocation), acquire the flock here.

    Raises
    ------
    PlanNotFoundError, JsonMalformedError, SchemaRejectedError,
    UnsupportedSchemaVersion, TemplateError, AtomicWriteError
        Surface verbatim; the dispatch path maps each to an exit code
        from `bin._render_plan.exit_codes`.
    """
    if assume_flock_held:
        _render_state_inner(plan_dir)
        return

    # Standalone path: acquire the state_flock here.
    # Import lazily to avoid a hard dep cycle (`_update_orchestrator` imports
    # from `_render_plan` heavily; we want the reverse arrow to be lazy).
    from bin._update_orchestrator.state_writer import state_flock

    with state_flock(plan_dir):
        _render_state_inner(plan_dir)


def _render_state_inner(plan_dir: Path) -> None:
    """The actual load-validate-render-write sequence.

    PRESUMES `state_flock(plan_dir)` is held (either by the caller or by
    `render_state_under_flock`'s own acquisition path).
    """
    state_path = Path(plan_dir) / "_state.json"
    md_path = Path(plan_dir) / "_orchestrator.md"

    # Step 1 — load + parse + schema-validate. Adapter infers required
    # top-level fields from the plan dir context so legacy minimal-shape
    # `_state.json` files (e.g. ctm-graph-wiring's `test_step.retry_count`)
    # still render cleanly.
    state = load_plan_json(state_path)
    state_for_render = _adapt_dict_tasks_to_list(state, plan_dir=plan_dir)
    validate_against_schema(
        state_for_render, "state", source_path=str(state_path)
    )

    # Step 2 — preserve operator notes from any existing MD.
    existing_md = read_existing_md(md_path)
    anchor_result = extract_anchor_content(existing_md)

    # Step 3 — render canonical body.
    rendered = render_canonical_body(
        state_for_render, "state", human_notes_content=anchor_result.content
    )

    # Step 4 — atomic write to `_orchestrator.md`.
    write_atomic(md_path, rendered)


_SLUG_PATTERN = "^[a-z0-9][a-z0-9_-]*$"


def _adapt_dict_tasks_to_list(state: dict, *, plan_dir: Path | None = None) -> dict:
    """Convert `tasks: {id: entry}` dict-form to `tasks: [entry]` list-form.

    The legacy `state_writer` mutates `state["tasks"]` as a dict keyed by
    task_id (per `bin/_update_orchestrator/state_writer.py::get_task_entry`).
    The v1 schema and the renderer both expect the v2.7 §E.2 list-form
    where each task carries its `id` inline. We adapt here, leaving the
    on-disk JSON untouched.

    If `tasks` is already a list, returns the state dict unchanged. If
    `tasks` is missing, returns the state with an empty list inserted so
    downstream code (renderer, schema validator) has the expected shape.
    Other shapes pass through unchanged — schema validation will catch
    them.

    Also infers required top-level fields (`schema_version`, `slug`,
    `phase`) when the on-disk state predates the schema's adoption. This
    is the bridge for the shipped minimal `_state.json` shape
    (ctm-graph-wiring's `test_step.retry_count` file) — the inference
    keeps the wiring functional without forcing every existing writer to
    migrate.

    Parameters
    ----------
    state : dict
        The parsed `_state.json` payload (as returned by `load_plan_json`).
    plan_dir : Path | None
        Optional path to the plan directory. Used to infer `slug` from
        `plan_dir.name` when the on-disk state omits the field.
    """
    if not isinstance(state, dict):
        return state
    out = dict(state)  # shallow copy; we mutate `tasks` and add defaults

    # Bridge: `_state.json` files predating the schema lack the metadata
    # the schema requires. Infer reasonable defaults so the render can
    # proceed; the on-disk file is NOT mutated.
    if "schema_version" not in out:
        out["schema_version"] = 1
    if "slug" not in out:
        # Infer slug from plan_dir name when available. The schema's
        # Slug pattern is `^[a-z0-9][a-z0-9_-]*$`; we coerce by checking
        # the inferred name against it and falling back to a sentinel.
        inferred_slug = plan_dir.name if plan_dir is not None else None
        if inferred_slug and _matches_slug_pattern(inferred_slug):
            out["slug"] = inferred_slug
        else:
            # Sentinel slug — schema-conforming but visibly non-real so
            # operators see the inference. Used only when both on-disk
            # `slug` and `plan_dir.name` are missing or non-conformant.
            out["slug"] = "unknown-plan"
    if "phase" not in out:
        # Free-form per schema; sentinel value carries the inference
        # signal forward to the rendered MD.
        out["phase"] = "unknown"

    tasks = out.get("tasks")
    if isinstance(tasks, dict):
        # Convert {id: entry} → [{id: id, **entry}].
        rebuilt: list[dict] = []
        for task_id, entry in tasks.items():
            if not isinstance(entry, dict):
                # Defensive: schema-mismatched entries pass through
                # untouched (validation will catch).
                rebuilt.append({"id": task_id, "_raw": entry})
                continue
            # Copy entry; ensure `id` field is present (entry may omit it
            # since the dict key already carries the id).
            merged: dict[str, Any] = {"id": task_id}
            merged.update(entry)
            # Schema requires `title`; infer from id if absent so the
            # render still produces a row (validation will catch genuine
            # malformed states upstream of this adapter).
            if "title" not in merged:
                merged["title"] = task_id
            # Schema requires `status` ∈ 7-status enum. Legacy minimal
            # state may omit status entirely (e.g., ctm-graph-wiring's
            # `test_step.retry_count` row carries only retry_count).
            # Default to `unknown` so the row still validates.
            if "status" not in merged:
                merged["status"] = "unknown"
            rebuilt.append(merged)
        out["tasks"] = rebuilt
        return out

    if tasks is None:
        out["tasks"] = []
        return out

    # tasks is a list (or something else) — leave alone.
    return out


def _matches_slug_pattern(s: str) -> bool:
    """Return True iff `s` matches the schema's Slug pattern."""
    import re

    return bool(re.match(_SLUG_PATTERN, s))
