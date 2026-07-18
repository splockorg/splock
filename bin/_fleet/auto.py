"""Automatic stage integration — the splock addition over the reference.

In qum, agents ran `fleet update` by hand at stage start/finish. In
splock, lifecycle tracking is a SIDE EFFECT of running a stage: the
stage engines (`bin/_planner`, `bin/_qa`, `bin/_retry_loop`,
`bin/_update_orchestrator`) and the prompt-orchestrated stage commands
(`/recon`, `/research`, `/qna`, `/code`) call these hooks on start
(`--status wip`) and completion (`--status ready --next <next stage>`,
or `done` / `blocked` / `closed`). Agents never hand-edit the hub.

Contract (what makes this safe to wire into every engine):

- **No-op unless opted in.** Every hook returns silently unless the
  project carries `docs/plans/_fleet/_fleet_meta.json` AND the slug dir
  exists. An un-adopted project is byte-for-byte unaffected.
- **Never raises.** A fleet failure must never fail a stage: hooks
  catch everything, emit one stderr warning, and return. Tracking is
  bookkeeping, not enforcement — the enforcement spine stays elsewhere.
- **Renders best-effort.** After each mutation the hub zones are
  regenerated; a hub with missing markers degrades to a warning (state
  files stay authoritative).
"""

from __future__ import annotations

import sys

from bin._fleet import engine, paths

#: Canonical pipeline order: the command an operator (or the chain) runs
#: after a stage completes cleanly. `review` is junction-scoped — see
#: BOUNDARY_NEXT.
STAGE_NEXT = {
    "recon": "/qa",
    "qa": "/plan",
    "research": "/plan",
    "qna": "/plan",
    "plan": "/implplan",
    "implplan": "/code",
    "code": "/test",
    "test": "/review",
    "review": None,
}

#: The phase-boundary review gate names the junction it cleared; the
#: next command follows from the junction, not from a linear order.
BOUNDARY_NEXT = {
    "plan_to_implplan": "/implplan",
    "implplan_to_code": "/code",
}


def _active(slug: str) -> bool:
    return paths.enabled() and paths.slug_dir(slug).is_dir()


def _warn(exc: BaseException) -> None:
    print(f"fleet: auto-update skipped ({type(exc).__name__}: {exc})", file=sys.stderr)


def _render_best_effort() -> None:
    try:
        engine.render_hub_write()
    except Exception as exc:  # noqa: BLE001 — tracking must not fail the stage
        _warn(exc)


def stage_started(slug: str, stage: str, *, actor: str | None = None,
                  note: str | None = None) -> None:
    """Stage entry: `wip`, with the in-flight command as the next action."""
    try:
        if not _active(slug):
            return
        engine.update(
            slug,
            stage=stage,
            status="wip",
            next_action=f"/{stage}",
            actor=actor or f"{stage}-engine",
            note=note or f"{stage} started",
        )
        _render_best_effort()
    except Exception as exc:  # noqa: BLE001
        _warn(exc)


def stage_finished(slug: str, stage: str, *, status: str = "ready",
                   next_action: str | None = None, actor: str | None = None,
                   note: str | None = None) -> None:
    """Clean stage completion: `ready --next <next stage>` (or `done`).

    Also clears the slug's stored `spawn_directive`: a directive targets
    the stage that just ran, so any stage completion consumes it — the
    prompt bay never advertises stale context for the NEXT stage.
    """
    try:
        if not _active(slug):
            return
        if next_action is None:
            next_action = STAGE_NEXT.get(stage)
        engine.update(
            slug,
            stage=stage,
            status=status,
            next_action=next_action or "—",
            actor=actor or f"{stage}-engine",
            note=note or f"{stage} completed",
            spawn_directive="",
        )
        _render_best_effort()
    except Exception as exc:  # noqa: BLE001
        _warn(exc)


def stage_blocked(slug: str, stage: str, *, note: str,
                  actor: str | None = None) -> None:
    """A verdict-carrying halt (review HALT, retry cap exhausted)."""
    try:
        if not _active(slug):
            return
        engine.update(
            slug,
            stage=stage,
            status="blocked",
            actor=actor or f"{stage}-engine",
            note=note,
        )
        _render_best_effort()
    except Exception as exc:  # noqa: BLE001
        _warn(exc)


def stage_event(slug: str, stage: str, *, note: str,
                actor: str | None = None) -> None:
    """Event-only append — no status flip.

    Used for infrastructure failures (SDK errors, driver crashes) where
    nothing about the slug's lifecycle actually changed, and for
    task-granular breadcrumbs inside the `code` stage.
    """
    try:
        if not _active(slug):
            return
        st = engine.load_state(slug) or {}
        engine.append_event(slug, {
            "ts": engine._now_iso(),
            "slug": slug,
            "stage": stage,
            "status": st.get("status"),
            "actor": actor or f"{stage}-engine",
            "note": note,
        })
        _render_best_effort()
    except Exception as exc:  # noqa: BLE001
        _warn(exc)


def code_task_updated(slug: str, task_id: str, status: str, *,
                      actor: str | None = None) -> None:
    """A `/code` task transition landed in `_state.json`.

    Any task movement means the code stage is in flight: one state
    write (`code` / `wip`) + one task-granular breadcrumb event. The
    stage-level `ready --next /test` flip happens when the task loop
    drains (commands/code.md) — the /test hook takes over from there
    regardless.
    """
    try:
        if not _active(slug):
            return
        engine.update(
            slug,
            stage="code",
            status="wip",
            next_action="/code",
            actor=actor or "update-orchestrator",
            note=f"task {task_id} → {status}",
        )
        _render_best_effort()
    except Exception as exc:  # noqa: BLE001
        _warn(exc)


def slug_closed(slug: str, *, note: str, actor: str | None = None) -> None:
    """The orchestrator was explicitly closed — archive the slug."""
    try:
        if not _active(slug):
            return
        engine.update(
            slug,
            status="closed",
            next_action="—",
            actor=actor or "update-orchestrator",
            note=note,
        )
        _render_best_effort()
    except Exception as exc:  # noqa: BLE001
        _warn(exc)
