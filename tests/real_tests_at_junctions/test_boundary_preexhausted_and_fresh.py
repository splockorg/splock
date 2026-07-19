"""Pre-exhausted boundary re-invocation: loud diagnosis + sanctioned reset.

Second boundary-machinery field defect (2026-07-19): after a
cap-exhaustion halt, the persisted per-boundary ``retry_count`` in the
sealed ``_state.json`` made an operator-direct ``bin/verify boundary``
re-invocation exit 17 with EMPTY stderr/stdout, append a second, empty
("Total iterations this halt: 0") morning-review entry, and spawn
nothing — indistinguishable from a transport failure in the field, and
with no legitimate reset short of the full chain-resume machinery (the
sealed-file hook correctly blocks a hand edit).

Pinned here:

1. `unified_counter_reset` — flock RMW, atomic write, returns the prior
   count (the sanctioned reset behind ``--fresh``);
2. a pre-exhausted `run_boundary_review` entry spawns NOTHING, writes
   NO new halt entry (halt_entry_path None), and prints the one-line
   stderr diagnosis;
3. mid-run exhaustion still writes its halt entry (the guard keys on
   "this run did no work", not on exhaustion itself);
4. the CLI: ``--fresh`` resets + logs to `_orchestrator_log.jsonl`; the
   pre-exhausted exit-17 path emits a structured stderr envelope.

Run from the splock repo root with the project venv active.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._retry_loop import iteration_loop, phase_boundary_review  # noqa: E402
from bin._retry_loop.main import _apply_fresh_reset  # noqa: E402

BOUNDARY = "implplan_to_code"
SLUG = "vk_slug"


def _seed_counter(plan_dir: Path, count: int) -> None:
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "_state.json").write_text(
        json.dumps({"tasks": {BOUNDARY: {"retry_count": count}}}),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# unified_counter_reset
# ---------------------------------------------------------------------------


def test_reset_returns_prior_and_zeroes_the_counter(tmp_path):
    _seed_counter(tmp_path, 3)
    assert iteration_loop.unified_counter_reset(tmp_path, task_id=BOUNDARY) == 3
    state = json.loads((tmp_path / "_state.json").read_text(encoding="utf-8"))
    assert state["tasks"][BOUNDARY]["retry_count"] == 0
    # remaining is restored to the full cap
    assert iteration_loop.unified_counter_get_remaining(
        tmp_path, task_id=BOUNDARY, cap=3) == 3


def test_reset_on_missing_state_is_a_clean_zero(tmp_path):
    assert iteration_loop.unified_counter_reset(tmp_path, task_id=BOUNDARY) == 0
    state = json.loads((tmp_path / "_state.json").read_text(encoding="utf-8"))
    assert state["tasks"][BOUNDARY]["retry_count"] == 0


def test_reset_preserves_unrelated_state(tmp_path):
    (tmp_path / "_state.json").write_text(json.dumps({
        "tasks": {BOUNDARY: {"retry_count": 2, "status": "wip"},
                  "T1": {"status": "done"}},
        "lifecycle": "active",
    }), encoding="utf-8")
    iteration_loop.unified_counter_reset(tmp_path, task_id=BOUNDARY)
    state = json.loads((tmp_path / "_state.json").read_text(encoding="utf-8"))
    assert state["tasks"][BOUNDARY] == {"retry_count": 0, "status": "wip"}
    assert state["tasks"]["T1"] == {"status": "done"}
    assert state["lifecycle"] == "active"


# ---------------------------------------------------------------------------
# pre-exhausted entry: no spawn, no empty halt entry, loud stderr
# ---------------------------------------------------------------------------


def _explode(*a, **k):
    raise AssertionError("must not be called on a pre-exhausted entry")


def test_preexhausted_entry_diagnoses_and_writes_nothing(tmp_path, capsys):
    _seed_counter(tmp_path, 3)
    verdict = phase_boundary_review.run_boundary_review(
        tmp_path,
        slug=SLUG,
        chain_id="manual_t",
        boundary=BOUNDARY,
        spawn_reviewer_fn=_explode,       # zero iterations spawned
        respawn_prior_step_fn=_explode,
        max_iterations=3,
    )
    assert verdict.terminal_shape == phase_boundary_review.TerminalShape.HALT
    assert verdict.counter_exhausted is True
    assert verdict.halt_entry_path is None       # no second, empty entry
    assert verdict.records == []
    assert not (tmp_path / "morning-review").exists()
    err = capsys.readouterr().err
    assert "pre-exhausted" in err and "--fresh" in err
    assert BOUNDARY in err and f"docs/plans/{SLUG}/morning-review/" in err


def test_reset_then_review_actually_spawns(tmp_path):
    """--fresh's whole point: after the reset, the review runs again."""
    _seed_counter(tmp_path, 3)
    iteration_loop.unified_counter_reset(tmp_path, task_id=BOUNDARY)
    calls = {"n": 0}

    def fake_reviewer(prompt, **kwargs):
        calls["n"] += 1
        return {
            "rubric_version": 1,
            "boundary": BOUNDARY,
            "terminal_shape": "READY",
            "R1_tests_enabled_consistency": "consistent",
            "R1_mismatched_task_ids": [],
            "R2_concrete_placeholders": "concrete",
            "R2_placeholder_sites": [],
            "R3_dag_topology": "dag",
            "R3_cycle_members": [],
            "R4_sealed_paths": "clean",
            "R4_flagged_references": [],
            "reviewer_notes": "clean",
        }

    verdict = phase_boundary_review.run_boundary_review(
        tmp_path,
        slug=SLUG,
        chain_id="manual_t",
        boundary=BOUNDARY,
        spawn_reviewer_fn=fake_reviewer,
        respawn_prior_step_fn=_explode,
        max_iterations=3,
    )
    assert calls["n"] == 1                      # the reviewer really ran
    assert verdict.terminal_shape == phase_boundary_review.TerminalShape.READY


# ---------------------------------------------------------------------------
# the CLI layer
# ---------------------------------------------------------------------------


def test_apply_fresh_reset_logs_the_intent(tmp_path, capsys):
    _seed_counter(tmp_path, 3)
    prior = _apply_fresh_reset(tmp_path, slug=SLUG, boundary=BOUNDARY,
                               chain_id="manual_t")
    assert prior == 3
    state = json.loads((tmp_path / "_state.json").read_text(encoding="utf-8"))
    assert state["tasks"][BOUNDARY]["retry_count"] == 0
    assert "retry_count 3 -> 0" in capsys.readouterr().err
    log = (tmp_path / "_orchestrator_log.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(l) for l in log.splitlines() if l.strip()]
    reset_rows = [r for r in rows if r.get("event_type") == "boundary_counter_reset"]
    assert len(reset_rows) == 1
    assert "operator --fresh reset" in reset_rows[0]["reason"]


def test_boundary_parser_accepts_fresh():
    from bin._retry_loop.main import _build_parser

    args = _build_parser().parse_args(
        ["boundary", SLUG, "--chain-id", "manual_t",
         "--boundary", BOUNDARY, "--fresh"])
    assert args.fresh is True
    args2 = _build_parser().parse_args(
        ["boundary", SLUG, "--chain-id", "manual_t", "--boundary", BOUNDARY])
    assert args2.fresh is False