"""Fleet concurrency — the collision-free-by-construction claim, exercised.

The design's whole point: per-slug files have no shared write target,
so any number of agents update concurrently with zero contention. These
tests run REAL separate processes (fork start-method: independent PIDs,
independent file descriptors) and assert the load-bearing invariants:

- N processes appending to one slug's `_fleet_log.jsonl` lose ZERO
  events — every line parses, every (writer, seq) pair lands exactly
  once (O_APPEND single-write atomicity for lines < PIPE_BUF);
- a reader polling `_fleet.json` during concurrent `update()` churn
  NEVER observes a torn/partial state file (tmp + `os.replace` swap);
- a renderer folding the whole tree during the churn never crashes and
  the final hub write is complete.

Run from the splock repo root with the project venv active.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._fleet import engine, hub, paths  # noqa: E402

SLUG = "contested_slug"
N_WRITERS = 8
N_EVENTS = 40

_ctx = multiprocessing.get_context("fork")


@pytest.fixture()
def fleet_project(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "adopter"
    (root / "docs" / "plans" / SLUG).mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    hub.init()
    return root


def _append_worker(project: str, worker_id: int, barrier) -> None:
    os.environ["CLAUDE_PROJECT_DIR"] = project
    barrier.wait()  # maximize interleaving: all writers start together
    for seq in range(N_EVENTS):
        engine.append_event(SLUG, {
            "ts": f"2026-07-18T00:00:{seq:02d}Z",
            "slug": SLUG,
            "stage": "code",
            "status": "wip",
            "actor": f"writer-{worker_id}",
            "note": f"w{worker_id}-e{seq}",
        })


def _update_worker(project: str, worker_id: int, barrier) -> None:
    os.environ["CLAUDE_PROJECT_DIR"] = project
    barrier.wait()
    for seq in range(N_EVENTS):
        engine.update(
            SLUG,
            stage="code",
            status="wip" if seq % 2 else "ready",
            actor=f"writer-{worker_id}",
            note=f"w{worker_id}-e{seq}",
        )


def _state_reader(project: str, barrier, failures) -> None:
    """Poll load_state during the churn; count torn/invalid reads."""
    os.environ["CLAUDE_PROJECT_DIR"] = project
    barrier.wait()
    for _ in range(300):
        try:
            st = engine.load_state(SLUG)
        except (json.JSONDecodeError, OSError):
            failures.value += 1
            continue
        if st is not None and "slug" not in st:
            failures.value += 1


def _renderer(project: str, barrier, failures) -> None:
    """Fold + hub-write repeatedly during the churn; count crashes."""
    os.environ["CLAUDE_PROJECT_DIR"] = project
    barrier.wait()
    for _ in range(30):
        try:
            engine.render_hub_write()
        except Exception:  # noqa: BLE001 — any crash is a failure
            failures.value += 1


def _run(procs) -> None:
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
    assert all(p.exitcode == 0 for p in procs), \
        f"worker exit codes: {[p.exitcode for p in procs]}"


def test_concurrent_appends_from_separate_processes_lose_zero_events(fleet_project):
    barrier = _ctx.Barrier(N_WRITERS)
    _run([
        _ctx.Process(target=_append_worker, args=(str(fleet_project), w, barrier))
        for w in range(N_WRITERS)
    ])

    lines = paths.log_path(SLUG).read_text(encoding="utf-8").splitlines()
    assert len(lines) == N_WRITERS * N_EVENTS  # nothing lost

    seen = set()
    for line in lines:
        e = json.loads(line)  # nothing torn: every line parses
        seen.add((e["actor"], e["note"]))
    assert len(seen) == N_WRITERS * N_EVENTS  # every event exactly once
    assert seen == {
        (f"writer-{w}", f"w{w}-e{s}")
        for w in range(N_WRITERS) for s in range(N_EVENTS)
    }


def test_concurrent_updates_with_reader_and_renderer(fleet_project):
    reader_failures = _ctx.Value("i", 0)
    render_failures = _ctx.Value("i", 0)
    barrier = _ctx.Barrier(N_WRITERS + 2)
    procs = [
        _ctx.Process(target=_update_worker, args=(str(fleet_project), w, barrier))
        for w in range(N_WRITERS)
    ]
    procs.append(_ctx.Process(
        target=_state_reader, args=(str(fleet_project), barrier, reader_failures)))
    procs.append(_ctx.Process(
        target=_renderer, args=(str(fleet_project), barrier, render_failures)))
    _run(procs)

    assert reader_failures.value == 0  # atomic swap: never a torn state read
    assert render_failures.value == 0  # the fold never crashes mid-churn

    # final state is one writer's LAST write, intact
    st = engine.load_state(SLUG)
    assert st["slug"] == SLUG and st["status"] in ("wip", "ready")
    # the log still carries every event from every writer
    lines = paths.log_path(SLUG).read_text(encoding="utf-8").splitlines()
    assert len(lines) == N_WRITERS * N_EVENTS
    assert all(json.loads(l) for l in lines)

    # ...and a final render lands a complete, well-formed hub
    engine.render_hub_write()
    text = paths.hub_path(engine.load_meta()).read_text(encoding="utf-8")
    for begin, end in engine.MARKERS.values():
        assert begin in text and end in text
    assert f"| `{SLUG}` |" in text


def test_concurrent_cross_slug_updates_have_zero_contention(fleet_project):
    """Distinct slugs — the production topology (one writer per path)."""
    slugs = [f"lane_{w}" for w in range(N_WRITERS)]
    for s in slugs:
        (paths.plans_dir() / s).mkdir()

    def worker(project: str, slug: str, barrier) -> None:
        os.environ["CLAUDE_PROJECT_DIR"] = project
        barrier.wait()
        for seq in range(N_EVENTS):
            engine.update(slug, stage="recon", status="ready",
                          next_action="/qa", actor=slug, note=f"e{seq}")

    barrier = _ctx.Barrier(N_WRITERS)
    _run([
        _ctx.Process(target=worker, args=(str(fleet_project), s, barrier))
        for s in slugs
    ])

    for s in slugs:
        assert engine.load_state(s)["actor"] == s
        lines = paths.log_path(s).read_text(encoding="utf-8").splitlines()
        assert len(lines) == N_EVENTS

    states = engine.load_all_states()
    assert set(slugs) <= set(states)
