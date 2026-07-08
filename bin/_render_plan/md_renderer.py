"""JSON → canonical MD body emission.

Per implplan §B.impl.4 step 6 (line 1114). The template is filled
deterministically — the same JSON input must produce byte-identical MD
output (idempotency property tested in `test_idempotency.py`).

Design choices for byte-stability:
- Dict iteration order follows the JSON field order, which we drive
  from a hand-coded section schedule rather than relying on Python's
  dict-ordering coincidence with the schema.
- All lists are emitted in their input order (no resorting).
- Trailing newline is enforced.
- Markdown list bullets use a single `-` + space, never `*`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal

from bin._env_paths import project_root
from .human_notes import (
    BOOTSTRAP_PLACEHOLDER,
    detect_outside_anchor_diff as _detect_outside_anchor_diff,
    wrap_in_anchors,
)

# Re-export the symbol so callers can use `md_renderer.detect_outside_anchor_diff`
# per the implplan §B.impl.4 function-signature table (line 1129).
detect_outside_anchor_diff = _detect_outside_anchor_diff


_TEMPLATES_REL = Path(".claude") / "templates"


def _template_path(filename: str) -> Path:
    """Resolve one template file: adopter-repo override first, plugin second.

    The fallback is PER FILE: an adopter repo may carry a
    ``.claude/templates/`` dir with its own unrelated templates without
    shadowing the splock set the plugin ships, and a file committed by the
    adopter overrides the shipped copy of the same name. In-tree checkouts
    resolve both candidates to the same file. When neither exists, the
    plugin-shipped path is returned so the caller's error names the
    canonical location.
    """
    project_candidate = project_root() / _TEMPLATES_REL / filename
    if project_candidate.is_file():
        return project_candidate
    plugin_root = Path(__file__).resolve().parents[2]
    return plugin_root / _TEMPLATES_REL / filename

PlanKind = Literal["plan", "orchestrator", "state"]


# 7-status glyph map per orch_status_render T2 (state_md_canonical.md.template
# status legend). Source of truth for the enum names: bin/_update_orchestrator/
# state_writer.py SEVEN_STATUS. The glyph ↔ status pairing is byte-equal to the
# template legend rows; both this dict and the template are independently
# verified by the T2 + T3 test suites.
_STATUS_GLYPHS: dict[str, str] = {
    "ready": "🕛",
    "wip": "✈️",
    "done": "✅",
    "deferred": "📅",
    "blocked": "❌",
    "cancelled": "🚫",
    "unknown": "❓",
}


class TemplateError(RuntimeError):
    """Raised on template-rendering failures.

    Maps to exit code 6 (`EXIT_TEMPLATE_ERROR`) per implplan §B.impl.4
    line 1155.
    """


def _load_template(kind: PlanKind) -> str:
    template_path = _template_path(f"{kind}_md_canonical.md.template")
    try:
        return template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TemplateError(f"cannot read template {template_path}: {exc}") from exc


def render_canonical_body(
    plan: dict,
    kind: PlanKind,
    *,
    human_notes_content: str = "",
) -> str:
    """Produce the canonical MD body from the validated JSON dict.

    `human_notes_content` is inserted inside the wrapped anchor block.
    Pass the extracted operator notes here on re-render; pass empty
    string on first render (the wrapper emits the bootstrap placeholder).
    """
    template = _load_template(kind)
    if kind == "plan":
        rendered = _render_plan_body(plan, template, human_notes_content)
    elif kind == "orchestrator":
        rendered = _render_orchestrator_body(plan, template, human_notes_content)
    elif kind == "state":
        rendered = _render_state_body(plan, template, human_notes_content)
    else:
        raise TemplateError(f"unknown plan kind: {kind!r}")
    # Enforce single trailing newline for byte-stability.
    return rendered.rstrip() + "\n"


def _render_plan_body(plan: dict, template: str, notes: str) -> str:
    try:
        return template.format(
            title=plan["title"],
            slug=plan["slug"],
            phase=plan["phase"],
            tier=plan["tier"],
            problem_statement=plan["problem_statement"],
            success_criteria_block=_format_success_criteria(plan["success_criteria"]),
            non_goals_block=_format_non_goals(plan.get("non_goals") or []),
            conceptual_architecture_block=_format_conceptual_architecture(
                plan.get("conceptual_architecture")
            ),
            tasks_skeleton_block=_format_tasks_skeleton(plan["tasks_skeleton"]),
            references_block=_format_references(plan.get("references") or []),
            human_notes_block=wrap_in_anchors(notes),
        )
    except KeyError as exc:
        raise TemplateError(f"plan missing required field: {exc}") from exc


def _render_orchestrator_body(plan: dict, template: str, notes: str) -> str:
    try:
        return template.format(
            slug=plan["slug"],
            phase=plan["phase"],
            plan_ref=plan["plan_ref"],
            tasks_block=_format_orch_tasks(plan["tasks"]),
            junctions_block=_format_junctions(plan.get("junctions") or []),
            review_agent_prompt_pointer_block=(
                plan.get("review_agent_prompt_pointer") or "_None._"
            ),
            human_notes_block=wrap_in_anchors(notes),
        )
    except KeyError as exc:
        raise TemplateError(f"orchestrator missing required field: {exc}") from exc


def _render_state_body(plan: dict, template: str, notes: str) -> str:
    """Render the canonical `_orchestrator.md` body from a `_state.json` dict.

    Unlike `_render_plan_body` / `_render_orchestrator_body`, the state
    template embeds the BEGIN/END human-notes anchor literals directly
    around the `{human_notes_block}` placeholder. Therefore we substitute
    the RAW notes content (or the bootstrap placeholder when empty) — we
    do NOT call `wrap_in_anchors` here, otherwise the anchors would
    appear twice in the output.

    Per orch_status_render T3:
    - `{rendered_at_iso8601}` — ISO-8601 UTC timestamp at render time.
    - `{rolled_up_phase_status}` — computed from the per-task status set.
    - `{cap_hit_banner_block}` — verbatim string from `cap_hit_banner` or `""`.
    - `{tasks_block}` — markdown table with status glyph + ID + title + retry.
    - `{human_notes_block}` — raw operator notes between embedded anchors.
    """
    try:
        tasks = plan.get("tasks") or []
        return template.format(
            slug=plan["slug"],
            schema_version=plan["schema_version"],
            phase=plan["phase"],
            rendered_at_iso8601=datetime.now(timezone.utc).isoformat(),
            rolled_up_phase_status=_compute_rolled_up_phase_status(tasks),
            cap_hit_banner_block=_format_banner_block(plan.get("cap_hit_banner")),
            tasks_block=_format_state_tasks(tasks),
            human_notes_block=_state_notes_content(notes),
        )
    except KeyError as exc:
        raise TemplateError(f"state missing required field: {exc}") from exc


def _state_notes_content(notes: str) -> str:
    """Return raw notes content (or bootstrap placeholder when empty).

    The state template's `{human_notes_block}` placeholder sits between
    literal BEGIN/END anchor lines; we substitute UNWRAPPED content here
    (the opposite of `_render_plan_body` / `_render_orchestrator_body`
    which substitute the wrapped form).
    """
    stripped = notes.strip() if notes else ""
    if not stripped:
        return BOOTSTRAP_PLACEHOLDER
    return stripped


def _compute_rolled_up_phase_status(tasks: Iterable[dict]) -> str:
    """Compute the phase-level rolled-up status from per-task statuses.

    Rules (per orch_status_render T3 contract):
    - empty tasks → "not started"
    - any `blocked` → "blocked" (wins over everything)
    - any `wip` or `unknown` → "in progress"
    - all in {done, deferred, cancelled} → "complete"
    - only `ready` → "not started"
    - otherwise (mix of `ready` + terminals) → "in progress"
    """
    statuses = {t.get("status") for t in tasks}
    if not statuses:
        return "not started"
    if "blocked" in statuses:
        return "blocked"
    if "wip" in statuses or "unknown" in statuses:
        return "in progress"
    terminal = {"done", "deferred", "cancelled"}
    if statuses <= terminal:
        return "complete"
    if statuses == {"ready"}:
        return "not started"
    # Remaining case: mix of `ready` + at least one of done/deferred/cancelled.
    return "in progress"


def _format_banner_block(banner: str | None) -> str:
    """Return the cap-hit banner content verbatim, or empty string when absent.

    Per orch_status_render T3: the banner is opaque renderer-surface
    content produced upstream (cap-enforcement subsystem). When the
    top-level `cap_hit_banner` field is absent or empty, the
    `{cap_hit_banner_block}` substitution yields `""` — the surrounding
    H2 section reads visually empty but the structure is preserved.
    """
    if banner is None:
        return ""
    if not isinstance(banner, str):
        # Defensive: schemas/_state_v1 currently has no `cap_hit_banner`
        # field, but per the schema's `additionalProperties: true` policy
        # a future writer could stash a non-string here. Coerce.
        return str(banner)
    return banner


def _format_state_tasks(tasks: Iterable[dict]) -> str:
    """Render the per-task markdown table for `{tasks_block}`.

    Columns: Status (glyph + name) | ID | Title | Retry count.

    Empty tasks list → table header only (no body rows). Unknown status
    values that bypassed schema validation (defensive) render the `❓`
    glyph; the schema enforces the closed 7-status enum upstream.
    """
    rows = [
        "| Status | ID | Title | Retry count |",
        "|---|---|---|---|",
    ]
    for task in tasks:
        status = task.get("status", "unknown")
        glyph = _STATUS_GLYPHS.get(status, _STATUS_GLYPHS["unknown"])
        task_id = task.get("id", "_none_")
        title = task.get("title", "_none_")
        retry_cell = _format_retry_cell(task)
        rows.append(f"| {glyph} {status} | {task_id} | {title} | {retry_cell} |")
    return "\n".join(rows)


def _format_retry_cell(task: dict) -> str:
    """Surface `develop_plan_telemetry.retry_count` when present.

    Per v2.7 §E.2 + state_v1.schema.json: the sidecar key path is
    `tasks[].develop_plan_telemetry.retry_count` (the chain driver's
    test-step retry loop). The shipped minimal `_state.json` shape also
    permits `tasks[].retry_count` directly — we check both, preferring
    the sidecar form.
    """
    sidecar = task.get("develop_plan_telemetry") or {}
    if isinstance(sidecar, dict) and "retry_count" in sidecar:
        return str(sidecar["retry_count"])
    if "retry_count" in task:
        return str(task["retry_count"])
    return "_none_"


# --------------------------------------------------------------------------- #
# Plan-section formatters
# --------------------------------------------------------------------------- #


def _format_success_criteria(items: Iterable[dict]) -> str:
    rows = [
        f"- **{item['id']}** — {item['criterion']}"
        for item in items
    ]
    return "\n".join(rows) if rows else "_None._"


def _format_non_goals(items: Iterable[str]) -> str:
    items = list(items)
    if not items:
        return "_None._"
    return "\n".join(f"- {entry}" for entry in items)


def _format_conceptual_architecture(arch: dict | None) -> str:
    if not arch:
        return "_None._"
    lines = [arch.get("overview", "").strip()]
    components = arch.get("components") or []
    if components:
        lines.append("")
        lines.append("### Components")
        lines.append("")
        for comp in components:
            deps = ", ".join(comp.get("dependencies") or []) or "_none_"
            lines.append(
                f"- **{comp['name']}** — {comp['purpose']} "
                f"(depends on: {deps})"
            )
    return "\n".join(line for line in lines if line is not None).strip()


def _format_tasks_skeleton(items: Iterable[dict]) -> str:
    rows = ["| ID | Title | Depends on |", "|---|---|---|"]
    for item in items:
        deps = ", ".join(item.get("depends_on") or []) or "_none_"
        rows.append(f"| {item['id']} | {item['title']} | {deps} |")
    return "\n".join(rows)


def _format_references(items: Iterable[dict]) -> str:
    items = list(items)
    if not items:
        return "_None._"
    return "\n".join(
        f"- **{ref['kind']}** → `{ref['pointer']}`" for ref in items
    )


# --------------------------------------------------------------------------- #
# Orchestrator-section formatters
# --------------------------------------------------------------------------- #


def _format_orch_tasks(tasks: Iterable[dict]) -> str:
    out: list[str] = []
    for task in tasks:
        out.append(f"### {task['id']} — {task['title']}")
        out.append("")
        out.append(
            f"- **Agent:** `{task['agent_assignment']['subagent']}` "
            f"(model: `{task['agent_assignment']['model']}`)"
        )
        if task.get("depends_on"):
            deps = ", ".join(task["depends_on"])
            out.append(f"- **Depends on:** {deps}")
        files = task.get("file_paths_touched") or []
        if files:
            file_list = ", ".join(f"`{p}`" for p in files)
            out.append(f"- **File paths touched:** {file_list}")
        else:
            out.append("- **File paths touched:** _none_")
        tests = task.get("tests_enabled") or []
        if tests:
            test_list = ", ".join(f"`{t}`" for t in tests)
            out.append(f"- **Tests enabled:** {test_list}")
        else:
            out.append("- **Tests enabled:** _none_")
        ddl = task.get("ddl_statements") or []
        if ddl:
            out.append("- **DDL statements:**")
            for stmt in ddl:
                out.append(f"  - `{stmt}`")
        call_sites = task.get("call_sites") or []
        if call_sites:
            out.append("- **Call sites:**")
            for site in call_sites:
                out.append(f"  - `{site}`")
        test_plan = task.get("test_plan") or []
        if test_plan:
            out.append("- **Test plan:**")
            for entry in test_plan:
                out.append(
                    f"  - `{entry['test_id']}` — asserts: {entry['asserts']} "
                    f"(fixture: `{entry['fixture']}`)"
                )
        out.append("")
    return "\n".join(out).rstrip()


def _format_junctions(items: Iterable[dict]) -> str:
    items = list(items)
    if not items:
        return "_None._"
    rows = ["| ID | After task | Kind |", "|---|---|---|"]
    for item in items:
        rows.append(
            f"| {item['id']} | {item['after_task']} | {item['kind']} |"
        )
    return "\n".join(rows)


__all__ = [
    "TemplateError",
    "PlanKind",
    "render_canonical_body",
    "detect_outside_anchor_diff",
]
