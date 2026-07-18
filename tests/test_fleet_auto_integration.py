"""Fleet auto-integration — stage tracking as a side effect of running a stage.

The contract under test (`bin/_fleet/auto.py` + the engine call sites):

- every hook is a SILENT NO-OP on a project that has not opted in
  (no files created, nothing raised);
- hooks NEVER raise into the calling engine — a broken hub degrades to
  a stderr warning while state files stay authoritative;
- stage start → `wip`; completion → `ready --next <canonical next>`;
  verdict halts → `blocked`; infrastructure errors → event-only;
- the real call sites fire: `bin/_qa/main.py` (stubbed SDK) and
  `bin/_retry_loop/main.py`'s tracked dispatchers (stubbed loop).

Run from the splock repo root with the project venv active.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._fleet import auto, engine, exit_codes, hub, paths  # noqa: E402
from bin._fleet.main import main as fleet_main  # noqa: E402

SLUG = "auto_slug"


@pytest.fixture()
def project(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "adopter"
    (root / "docs" / "plans" / SLUG).mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


@pytest.fixture()
def fleet_project(project) -> Path:
    hub.init()
    return project


# ---------------------------------------------------------------------------
# the no-op + never-raise contract
# ---------------------------------------------------------------------------


def test_hooks_are_noops_when_not_opted_in(project):
    auto.stage_started(SLUG, "recon")
    auto.stage_finished(SLUG, "recon")
    auto.stage_blocked(SLUG, "test", note="n")
    auto.stage_event(SLUG, "test", note="n")
    auto.code_task_updated(SLUG, "T1", "ready")
    auto.slug_closed(SLUG, note="n")
    assert not paths.state_path(SLUG).exists()
    assert not paths.log_path(SLUG).exists()


def test_hooks_are_noops_for_unknown_slug(fleet_project):
    auto.stage_started("no_such_slug", "recon")
    assert not paths.state_path("no_such_slug").exists()


def test_hooks_never_raise_on_broken_hub(fleet_project, capsys):
    # strip the markers so every render fails
    hub_file = paths.hub_path(engine.load_meta())
    hub_file.write_text("# broken hub\n", encoding="utf-8")
    auto.stage_started(SLUG, "recon")  # must not raise
    assert engine.load_state(SLUG)["status"] == "wip"  # state still landed
    assert "fleet: auto-update skipped" in capsys.readouterr().err


def test_stage_finished_clears_consumed_spawn_directive(fleet_project):
    """A stored directive targets the stage that just ran: completion
    consumes it (the prompt bay must never advertise stale context), while
    a blocked halt keeps it for the retry/resume."""
    engine.update(SLUG, stage="plan", status="ready", next_action="/plan",
                  actor="op", spawn_directive="ingest the qa recs")
    auto.stage_blocked(SLUG, "plan", note="halted")
    assert engine.load_state(SLUG)["spawn_directive"] == "ingest the qa recs"
    auto.stage_finished(SLUG, "plan")
    state = engine.load_state(SLUG)
    assert state["status"] == "ready" and state["next"] == "/implplan"
    assert state["spawn_directive"] == ""


# ---------------------------------------------------------------------------
# the lifecycle verbs
# ---------------------------------------------------------------------------


def test_started_then_finished_walks_the_pipeline(fleet_project):
    auto.stage_started(SLUG, "recon", actor="recon-agent")
    st = engine.load_state(SLUG)
    assert (st["stage"], st["status"], st["next"]) == ("recon", "wip", "/recon")

    auto.stage_finished(SLUG, "recon", actor="recon-agent", note="authored")
    st = engine.load_state(SLUG)
    assert (st["status"], st["next"]) == ("ready", "/qa")  # canonical next

    # ...and the hub zones re-rendered as a side effect
    text = paths.hub_path(engine.load_meta()).read_text(encoding="utf-8")
    assert f"| `{SLUG}` | recon | /qa | 🕛 ready |" in text


def test_finished_with_override_and_done(fleet_project):
    auto.stage_started(SLUG, "review")
    auto.stage_finished(SLUG, "review", status="done", next_action="closeout")
    st = engine.load_state(SLUG)
    assert (st["status"], st["next"]) == ("done", "closeout")


def test_blocked_and_closed(fleet_project):
    auto.stage_started(SLUG, "test")
    auto.stage_blocked(SLUG, "test", note="retry cap exhausted")
    assert engine.load_state(SLUG)["status"] == "blocked"
    auto.slug_closed(SLUG, note="archived")
    st = engine.load_state(SLUG)
    assert (st["status"], st["next"]) == ("closed", "—")


def test_stage_event_appends_without_status_flip(fleet_project):
    auto.stage_started(SLUG, "test")
    auto.stage_event(SLUG, "test", note="test-step errored (exit 2)")
    assert engine.load_state(SLUG)["status"] == "wip"  # unchanged
    notes = [e["note"] for e in engine.load_all_events()]
    assert "test-step errored (exit 2)" in notes


def test_code_task_updated_is_task_granular(fleet_project):
    auto.code_task_updated(SLUG, "T3", "ready")
    st = engine.load_state(SLUG)
    assert (st["stage"], st["status"], st["next"]) == ("code", "wip", "/code")
    assert engine.load_all_events()[-1]["note"] == "task T3 → ready"


def test_stage_cli_verb_is_forgiving(project, fleet_project, capsys):
    # `stage` exits 0 (skip note on stderr) — safe to run unconditionally
    rc = fleet_main(["stage", "start", "missing_slug", "--stage", "recon"])
    assert rc == exit_codes.EXIT_OK
    assert "skipping (opt-in)" in capsys.readouterr().err

    assert fleet_main(["stage", "start", SLUG, "--stage", "recon"]) == 0
    assert engine.load_state(SLUG)["status"] == "wip"
    assert fleet_main([
        "stage", "finish", SLUG, "--stage", "recon", "--note", "done!",
    ]) == 0
    st = engine.load_state(SLUG)
    assert (st["status"], st["next"]) == ("ready", "/qa")


# ---------------------------------------------------------------------------
# real call sites
# ---------------------------------------------------------------------------


def test_qa_engine_records_start_and_finish(fleet_project, monkeypatch):
    from bin._qa import main as qa_main
    from bin._qa.invoke import QaResult

    # the qa predecessor gate needs a non-empty recon artifact
    (paths.slug_dir(SLUG) / f"{SLUG}_recon.md").write_text(
        "## findings\nreal enough\n", encoding="utf-8"
    )
    monkeypatch.setattr(qa_main, "_PLANS_DIR", paths.plans_dir())

    seen: dict = {}

    def fake_invoke(**kwargs):
        seen["mid_run_status"] = engine.load_state(SLUG)["status"]
        return QaResult(qa_md="## qa\nfindings\n", cost_usd=0.0, model_id="stub")

    monkeypatch.setattr(qa_main, "invoke_qa", fake_invoke)

    rc = qa_main.main(["qa", SLUG])
    assert rc == 0
    assert seen["mid_run_status"] == "wip"  # start hook fired before the SDK call
    st = engine.load_state(SLUG)
    assert (st["stage"], st["status"], st["next"]) == ("qa", "ready", "/plan")


@pytest.mark.parametrize(
    "rc,expected_status,expected_next",
    [
        (0, "ready", "/review"),
        (17, "blocked", "/test"),   # retry cap → blocked (next untouched)
        (2, "wip", "/test"),        # driver crash → event-only, stays wip
    ],
)
def test_retry_loop_test_step_mapping(fleet_project, monkeypatch,
                                      rc, expected_status, expected_next):
    from bin._retry_loop import main as rl_main

    monkeypatch.setattr(rl_main, "_run_test_step", lambda args: rc)
    ns = argparse.Namespace(subcommand="test-step", slug=SLUG,
                            chain_id="manual_t", max_retries=None)
    assert rl_main._run_test_step_tracked(ns) == rc
    st = engine.load_state(SLUG)
    assert (st["stage"], st["status"], st["next"]) == ("test", expected_status, expected_next)


def test_retry_loop_boundary_junction_next(fleet_project, monkeypatch):
    from bin._retry_loop import main as rl_main

    monkeypatch.setattr(rl_main, "_run_boundary", lambda args: 0)
    ns = argparse.Namespace(subcommand="boundary", slug=SLUG,
                            chain_id="manual_t", boundary="plan_to_implplan")
    assert rl_main._run_boundary_tracked(ns) == 0
    st = engine.load_state(SLUG)
    assert (st["stage"], st["status"], st["next"]) == ("review", "ready", "/implplan")
    events = engine.load_all_events()
    assert events[-1]["note"] == "plan_to_implplan review READY"
