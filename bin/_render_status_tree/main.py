"""CLI + library entry for `bin/render_status_tree`.

Reads `<slug>_orchestrator.json` (DAG: tasks + depends_on + junctions)
and `_state.json` (per-task canonical status + optional notes), emits the
combined-view `<slug>_orchestrator_execution_tree.md` to the same plan dir.

Output sections:
    1. Header — slug, rendered_at, phase, summary chips
    2. Currently — wip / blocked highlights, or next-ready candidates
    3. Execution waves — ASCII tree (├── / └── / │) grouped by topological
       depth. Junctions nest under their gating task. Renders inside a
       ```text fenced block so it survives every MD viewer (PyCharm,
       VSCode, Cursor) without a Mermaid plugin.
    4. Tasks — wide tabular view (id, status, wave, deps, title, notes)

Optional schema fields (renderer tolerates absence):
    orchestrator.json::tasks[*].wave_label  → per-wave header label
    _state.json::tasks[<id>].notes          → Notes column body

Exit codes:
    0  render success
    1  missing orchestrator JSON (warning, empty MD stub)
    2  corrupt JSON detected (parse error)
    3  output write failed
    4  argparse failure

CLI surface:
    bin/render_status_tree <slug>
    bin/render_status_tree --all
    bin/render_status_tree --output <path> <slug>
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
import tempfile
from collections import Counter
from typing import Sequence


STATUS_GLYPHS = {
    "ready": "🕛",
    "wip": "✈️",
    "done": "✅",
    "deferred": "📅",
    "blocked": "❌",
    "cancelled": "🚫",
    "unknown": "❓",
}

JUNCTION_GLYPHS = {
    "review_gate": "⚖️",
    "test_gate": "🧪",
    "phase_boundary": "🚧",
}

EXIT_OK = 0
EXIT_MISSING_ORCH_JSON = 1
EXIT_CORRUPT_JSON = 2
EXIT_WRITE_FAILED = 3
EXIT_USAGE = 4


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DOCS_PLANS = REPO_ROOT / "docs" / "plans"

OUTPUT_FILENAME_SUFFIX = "_orchestrator_execution_tree.md"

TITLE_TREE_MAX = 80
TITLE_TABLE_MAX = 90
WAVE_HEADER_WIDTH = 64


def _build_parser() -> argparse.ArgumentParser:
    # REQ_E: allow_abbrev=False per `bin/_cli_lint` standing requirement.
    # REQ_C exemption (renderer-by-design): catalog audit comes from the
    # writer chain (bin/update_orchestrator → _invoke_render_status_tree);
    # this CLI emits no side-effects beyond writing its derived MD.
    p = argparse.ArgumentParser(
        prog="bin/render_status_tree",
        description=(
            "Render <slug>_orchestrator_execution_tree.md — ASCII wave tree + "
            "tasks table, combining <slug>_orchestrator.json + _state.json."
        ),
        allow_abbrev=False,
    )
    p.add_argument("slug", nargs="?", help="Plan slug (dir under docs/plans/).")
    p.add_argument("--all", action="store_true", help="Render every plan dir.")
    p.add_argument(
        "--output",
        type=pathlib.Path,
        default=None,
        help="Override output path (only valid with a single slug).",
    )
    return p


def _status_for(state: dict, task_id: str) -> str:
    tasks = state.get("tasks") if isinstance(state, dict) else None
    if isinstance(tasks, dict):
        entry = tasks.get(task_id)
    elif isinstance(tasks, list):
        entry = next(
            (t for t in tasks if isinstance(t, dict) and t.get("id") == task_id),
            None,
        )
    else:
        entry = None
    if isinstance(entry, dict):
        status = entry.get("status")
        if isinstance(status, str):
            return status
    return "ready"


def _notes_for(state: dict, task_id: str) -> str:
    tasks = state.get("tasks") if isinstance(state, dict) else None
    if isinstance(tasks, dict):
        entry = tasks.get(task_id)
        if isinstance(entry, dict):
            notes = entry.get("notes")
            if isinstance(notes, str):
                return notes
    return ""


def _build_dag(
    tasks_list: list[dict],
) -> tuple[list[str], dict[str, list[str]], dict[str, list[str]]]:
    """Return (roots, children_by_parent, parents_by_child)."""
    parents: dict[str, list[str]] = {}
    children: dict[str, list[str]] = {}
    for t in tasks_list:
        tid = t.get("id")
        if not isinstance(tid, str):
            continue
        parents[tid] = list(t.get("depends_on") or [])
        children.setdefault(tid, [])
    for tid, deps in parents.items():
        for dep_id in deps:
            children.setdefault(dep_id, []).append(tid)
    roots = [tid for tid in parents if not parents[tid]]
    return roots, children, parents


def _topological_depths(
    parents: dict[str, list[str]],
) -> dict[str, int]:
    """Compute depth of each task (max parent depth + 1; roots = 0)."""
    depths: dict[str, int] = {}

    def depth(node_id: str, visiting: set[str]) -> int:
        if node_id in depths:
            return depths[node_id]
        if node_id in visiting:
            return 0
        visiting.add(node_id)
        parent_ids = parents.get(node_id, [])
        if not parent_ids:
            d = 0
        else:
            d = max(depth(p, visiting) for p in parent_ids) + 1
        depths[node_id] = d
        visiting.discard(node_id)
        return d

    for nid in parents:
        depth(nid, set())
    return depths


def _group_by_wave(
    depths: dict[str, int],
    task_order: list[str],
) -> dict[int, list[str]]:
    """Group task ids by depth. Preserve orchestrator-JSON order within a wave."""
    order_index = {tid: i for i, tid in enumerate(task_order)}
    waves: dict[int, list[str]] = {}
    for node_id, d in depths.items():
        waves.setdefault(d, []).append(node_id)
    for w in waves:
        waves[w].sort(key=lambda t: order_index.get(t, 1_000_000))
    return waves


def _truncate(title: str, max_len: int) -> str:
    if len(title) <= max_len:
        return title
    return title[: max_len - 1].rstrip() + "…"


def _backtick_safe(text: str) -> str:
    if "`" not in text:
        return f"`{text}`"
    return f"`` {text} ``"


def _md_escape_pipe(text: str) -> str:
    return text.replace("|", "\\|")


def _render_header(
    slug: str,
    rendered_at: str,
    phase: str,
    tasks_list: list[dict],
    state: dict,
    junctions_list: list[dict],
) -> list[str]:
    total = len(tasks_list)
    status_counter = Counter(_status_for(state, t["id"]) for t in tasks_list if "id" in t)
    done = status_counter.get("done", 0)
    wip = status_counter.get("wip", 0)
    cancelled = status_counter.get("cancelled", 0)
    ready = status_counter.get("ready", 0)
    blocked = status_counter.get("blocked", 0)
    deferred = status_counter.get("deferred", 0)
    pct = round(done / total * 100) if total else 0

    chips = [f"Phase {phase}" if not str(phase).lower().startswith("phase") else str(phase)]
    chips.append(f"{pct}% done ({done}/{total})")
    if junctions_list:
        chips.append(f"{len(junctions_list)} junctions")
    if wip:
        chips.append(f"{wip} ✈️ wip")
    if blocked:
        chips.append(f"{blocked} ❌ blocked")
    if ready and (wip or blocked):
        chips.append(f"{ready} 🕛 ready")
    if deferred:
        chips.append(f"{deferred} 📅 deferred")
    if cancelled:
        chips.append(f"{cancelled} 🚫 cancelled")

    return [
        f"# `{slug}` — orchestrator execution tree",
        "",
        f"> {' · '.join(chips)}",
        f"> Updated `{rendered_at}` by `bin/render_status_tree`",
        "",
        "Legend: ✅ done · ✈️ wip · 🕛 ready · 📅 deferred · ❌ blocked · 🚫 cancelled  ",
        "        🧪 test_gate · ⚖️ review_gate · 🚧 phase_boundary",
        "",
        "<!-- Auto-generated — do not edit by hand. Regenerated on every bin/update_orchestrator call. -->",
        "",
    ]


def _render_now_section(
    tasks_list: list[dict],
    parents: dict[str, list[str]],
    junctions_by_task: dict[str, list[dict]],
    state: dict,
) -> list[str]:
    titles = {t["id"]: t.get("title", t["id"]) for t in tasks_list if "id" in t}

    wip_tasks = [t for t in tasks_list if _status_for(state, t.get("id", "")) == "wip"]
    ready_tasks = [t for t in tasks_list if _status_for(state, t.get("id", "")) == "ready"]
    blocked_tasks = [t for t in tasks_list if _status_for(state, t.get("id", "")) == "blocked"]

    if not (wip_tasks or ready_tasks or blocked_tasks):
        return [
            "## Currently",
            "",
            "_Nothing in progress — all tasks are done, cancelled, or deferred._",
            "",
        ]

    lines = ["## Currently", ""]

    def _task_line(t: dict, status_label: str, glyph: str) -> str:
        tid = t.get("id", "?")
        title = titles.get(tid, tid)
        deps = parents.get(tid, [])
        next_juncs = junctions_by_task.get(tid, [])
        parts = [f"**{glyph} `{tid}` {status_label}** — {_backtick_safe(title)}"]
        if deps:
            dep_chips = ", ".join(
                f"`{d}` ({STATUS_GLYPHS.get(_status_for(state, d), '❓')})"
                for d in deps
            )
            parts.append(f"depends on {dep_chips}")
        if next_juncs:
            nxt = ", ".join(
                f"`{j['id']}` ({JUNCTION_GLYPHS.get(j.get('kind', '?'), '⚙️')} {j.get('kind', '?')})"
                for j in next_juncs
            )
            parts.append(f"next gate: {nxt}")
        return "- " + " · ".join(parts)

    for t in wip_tasks:
        lines.append(_task_line(t, "in progress", "✈️"))
    for t in blocked_tasks:
        lines.append(_task_line(t, "blocked", "❌"))
    if not wip_tasks and not blocked_tasks:
        for t in ready_tasks[:3]:
            lines.append(_task_line(t, "ready to start", "🕛"))
    lines.append("")
    return lines


def _render_waves_tree_section(
    tasks_list: list[dict],
    parents: dict[str, list[str]],
    junctions_by_task: dict[str, list[dict]],
    state: dict,
) -> list[str]:
    """Render the DAG as an ASCII tree grouped by topological wave.

    Inside a ```text fenced block so the tree characters (├── └── │) render
    verbatim in every Markdown viewer. Each wave is a block of sibling
    tasks; junctions for a task hang as nested children with their own
    branch characters. Wave labels (optional `wave_label` per task in
    orchestrator.json) appear in the wave header when present.
    """
    titles = {t["id"]: t.get("title", t["id"]) for t in tasks_list if "id" in t}
    wave_label_by_task = {
        t["id"]: t.get("wave_label", "")
        for t in tasks_list
        if isinstance(t.get("id"), str)
    }
    task_order = [t["id"] for t in tasks_list if isinstance(t.get("id"), str)]
    depths = _topological_depths(parents)
    waves = _group_by_wave(depths, task_order)

    lines = ["## Execution waves", ""]
    if not waves:
        lines.extend(["_No tasks to render._", ""])
        return lines

    lines.extend([
        "Each wave is a set of tasks with no dependency between them — they "
        "can run in parallel. Junctions (`🧪` / `⚖️` / `🚧`) nest under the "
        "task they gate.",
        "",
        "```text",
    ])

    sorted_waves = sorted(waves.keys())
    for w_idx, w in enumerate(sorted_waves):
        ids = waves[w]
        label = ""
        for tid in ids:
            v = wave_label_by_task.get(tid, "")
            if isinstance(v, str) and v.strip():
                label = v.strip()
                break
        count = len(ids)
        plural = "parallel" if count > 1 else "task"
        suffix = f"({count} {plural})"
        if label:
            header_text = f"Wave {w} — {label} {suffix}"
        else:
            header_text = f"Wave {w} {suffix}"
        pad_len = max(3, WAVE_HEADER_WIDTH - len(header_text) - 1)
        lines.append(f"{header_text} {'─' * pad_len}")

        for t_idx, tid in enumerate(ids):
            is_last_task = t_idx == len(ids) - 1
            task_branch = "└──" if is_last_task else "├──"
            indent_after_task = "    " if is_last_task else "│   "
            status = _status_for(state, tid)
            glyph = STATUS_GLYPHS.get(status, "❓")
            title = _truncate(titles.get(tid, tid), TITLE_TREE_MAX)
            lines.append(f"{task_branch} {glyph} {tid}  {title}")

            juncs = junctions_by_task.get(tid, [])
            for j_idx, j_meta in enumerate(juncs):
                jid = j_meta.get("id", "?")
                kind = j_meta.get("kind", "?")
                jglyph = JUNCTION_GLYPHS.get(kind, "⚙️")
                is_last_junc = j_idx == len(juncs) - 1
                junc_branch = "└──" if is_last_junc else "├──"
                lines.append(f"{indent_after_task}{junc_branch} {jglyph} {jid} ({kind})")

        if w_idx < len(sorted_waves) - 1:
            lines.append("")

    lines.extend(["```", ""])
    return lines


def _render_task_detail_section(
    tasks_list: list[dict],
    parents: dict[str, list[str]],
    state: dict,
) -> list[str]:
    task_order = [t["id"] for t in tasks_list if isinstance(t.get("id"), str)]
    depths = _topological_depths(parents)
    any_notes = any(
        _notes_for(state, t["id"]).strip()
        for t in tasks_list if "id" in t
    )

    if any_notes:
        header = "| ID | Status | Wave | Deps | Title | Notes |"
        rule = "|---|---|---|---|---|---|"
    else:
        header = "| ID | Status | Wave | Deps | Title |"
        rule = "|---|---|---|---|---|"

    lines = ["## Tasks", "", header, rule]
    for t in tasks_list:
        tid = t.get("id")
        if not isinstance(tid, str):
            continue
        title = t.get("title", tid)
        status = _status_for(state, tid)
        glyph = STATUS_GLYPHS.get(status, "❓")
        wave = depths.get(tid, "?")
        deps = parents.get(tid, [])
        deps_cell = ", ".join(f"`{d}`" for d in deps) if deps else "—"
        title_cell = _md_escape_pipe(_backtick_safe(_truncate(title, TITLE_TABLE_MAX)))
        if any_notes:
            notes = _notes_for(state, tid).strip()
            notes_cell = _md_escape_pipe(notes) if notes else ""
            lines.append(
                f"| `{tid}` | {glyph} {status} | {wave} | {deps_cell} | {title_cell} | {notes_cell} |"
            )
        else:
            lines.append(
                f"| `{tid}` | {glyph} {status} | {wave} | {deps_cell} | {title_cell} |"
            )
    lines.append("")
    return lines


def _atomic_write(target: pathlib.Path, body: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=target.parent,
        prefix=".__render_status_tree__",
        suffix=".md.tmp",
        delete=False,
    )
    try:
        tmp.write(body)
        tmp.flush()
        import os
        os.fsync(tmp.fileno())
        tmp.close()
        pathlib.Path(tmp.name).replace(target)
    except Exception:
        tmp.close()
        try:
            pathlib.Path(tmp.name).unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _render_one(
    plan_dir: pathlib.Path,
    *,
    output: pathlib.Path | None,
) -> int:
    slug = plan_dir.name
    orch_path = plan_dir / f"{slug}_orchestrator.json"
    state_path = plan_dir / "_state.json"
    out_path = output or (plan_dir / f"{slug}{OUTPUT_FILENAME_SUFFIX}")

    rendered_at = (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    if not orch_path.exists():
        body = "\n".join(
            _render_header(
                slug, rendered_at, "unknown",
                tasks_list=[], state={}, junctions_list=[],
            )
            + ["_(no orchestrator JSON found — nothing to render)_"]
        ) + "\n"
        try:
            _atomic_write(out_path, body)
        except OSError:
            return EXIT_WRITE_FAILED
        return EXIT_MISSING_ORCH_JSON

    try:
        orch = json.loads(orch_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return EXIT_CORRUPT_JSON

    try:
        state = (
            json.loads(state_path.read_text(encoding="utf-8"))
            if state_path.exists()
            else {}
        )
    except (json.JSONDecodeError, OSError):
        state = {}

    tasks_list = orch.get("tasks") or []
    junctions_list = orch.get("junctions") or []
    phase = orch.get("phase", "unknown")

    _, _, parents = _build_dag(tasks_list)
    junctions_by_task: dict[str, list[dict]] = {}
    for j in junctions_list:
        after = j.get("after_task")
        if isinstance(after, str):
            junctions_by_task.setdefault(after, []).append(j)

    body_lines = (
        _render_header(
            slug, rendered_at, phase,
            tasks_list, state, junctions_list,
        )
        + _render_now_section(tasks_list, parents, junctions_by_task, state)
        + _render_waves_tree_section(
            tasks_list, parents, junctions_by_task, state,
        )
        + _render_task_detail_section(tasks_list, parents, state)
    )
    body = "\n".join(body_lines) + "\n"

    try:
        _atomic_write(out_path, body)
    except OSError:
        return EXIT_WRITE_FAILED
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code != 0 else EXIT_OK

    if args.all and args.slug:
        print("error: --all is mutually exclusive with a positional slug", file=sys.stderr)
        return EXIT_USAGE
    if args.all and args.output:
        print("error: --output is only valid with a single slug", file=sys.stderr)
        return EXIT_USAGE
    if not args.all and not args.slug:
        parser.print_usage(sys.stderr)
        return EXIT_USAGE

    if args.all:
        worst = EXIT_OK
        if not DOCS_PLANS.exists():
            return EXIT_OK
        # Walk both active plans (docs/plans/<slug>/) and closed plans
        # (docs/plans/_closed/<slug>/). Closed plans are immutable in
        # practice, but the renderer itself changes — re-rendering them
        # keeps their execution-tree MD aligned with the current schema.
        candidate_roots = [DOCS_PLANS]
        closed_root = DOCS_PLANS / "_closed"
        if closed_root.is_dir():
            candidate_roots.append(closed_root)
        for root in candidate_roots:
            for slug_dir in sorted(root.iterdir()):
                if not slug_dir.is_dir():
                    continue
                if slug_dir.name == "_closed":
                    continue  # handled by the explicit candidate_roots entry
                orch_json = slug_dir / f"{slug_dir.name}_orchestrator.json"
                if not orch_json.exists():
                    continue
                code = _render_one(slug_dir, output=None)
                if code > worst:
                    worst = code
        return worst

    slug_dir = DOCS_PLANS / args.slug
    return _render_one(slug_dir, output=args.output)


if __name__ == "__main__":
    sys.exit(main())
