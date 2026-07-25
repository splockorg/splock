"""The amend write+render transaction, and its rollback (plan_surgical_amend T6f).

An amend is the ONE planner path where the JSON write and the MD-twin render must
be atomic with respect to each other: it patches a plan that may already back a
downstream orchestrator, so a JSON that advances while its twin does not leaves
the two artifacts DIVERGED — the yaml_refactor drift class the whole surgical
substrate exists to prevent. `bin/_planner/main.py` therefore captures the exact
pre-amend bytes before the write and restores them if the render fails.

Until now that transaction had no test. It was believed to need an
API-key-bearing environment to exercise, which was false twice over: splock's
planner runs on the Claude Code subscription transport and reads no
`ANTHROPIC_API_KEY` at all (see `test_planner_subscription_transport.py`), and a
transaction test should not make a live model call regardless — determinism is
the point.

**Seam disclosure, because it bounds what these tests prove.** The transaction
test stubs `invoke_planner` — the module boundary between "obtain a patch from
the model" and "apply, persist, render, and unwind on failure". That is the right
seam for this transaction, which is agnostic to where the patch came from. But it
means **these tests say nothing about the transport**. A regression that put the
planner back on a metered API key would not fail anything here. The transport is
pinned separately, including the metered-credential scrub that keeps billing on
the subscription — `test_planner_subscription_transport.py`. Neither file is
sufficient alone; this note exists so a future reader does not mistake one for
the other.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bin._planner import exit_codes
from bin._planner.main import _rollback_json


# ---------------------------------------------------------------------------
# `_rollback_json` — a pure function over (path, bytes). No stubs at all, so
# these are the strongest tests in the file.
# ---------------------------------------------------------------------------


def test_rollback_restores_the_exact_prior_bytes(tmp_path: Path):
    """Byte-equality is the invariant, not JSON-equality.

    Restoring semantically-equal-but-reserialized JSON would silently reformat
    the file — which is precisely the drift class the surgical substrate exists
    to prevent, arriving through the rollback that was supposed to prevent it.
    """
    target = tmp_path / "slug_plan.json"
    # Deliberately NOT the canonical sort_keys/indent=2 form: if the rollback
    # round-trips through json.dumps, this fails.
    prior = b'{\n    "b": 2,\n  "a": 1\n}\n'
    target.write_bytes(prior)

    pre = target.read_bytes()
    target.write_text('{"a": 999}')  # the amend advances the file

    assert _rollback_json(target, pre) is True
    assert target.read_bytes() == prior


def test_rollback_reports_failure_instead_of_raising(tmp_path: Path):
    """The rollback is the last line of the transaction; if it raises, the CLI
    loses the chance to tell the operator that the unwind ITSELF failed — the one
    state where neither artifact can be trusted. It must return False."""
    target = tmp_path / "missing_dir" / "slug_plan.json"  # parent does not exist

    assert _rollback_json(target, b'{"a": 1}') is False


def test_rollback_refuses_undecodable_bytes_without_raising(tmp_path: Path):
    target = tmp_path / "slug_plan.json"
    target.write_text("{}")

    assert _rollback_json(target, b"\xff\xfe not utf-8") is False


def test_rollback_leaves_no_partial_file_on_success(tmp_path: Path):
    """It restores through the same atomic writer the forward write used, so the
    restore is itself crash-safe. Assert the observable consequence: exactly one
    file, no temp residue."""
    target = tmp_path / "slug_plan.json"
    target.write_bytes(b'{"a": 1}')
    pre = target.read_bytes()
    target.write_bytes(b'{"a": 2}')

    assert _rollback_json(target, pre) is True
    assert sorted(p.name for p in tmp_path.iterdir()) == ["slug_plan.json"]


# ---------------------------------------------------------------------------
# The transaction. Stubs `invoke_planner` (see the seam disclosure above) and
# forces the render to fail.
# ---------------------------------------------------------------------------


def _valid_plan(slug: str) -> dict:
    return {
        "schema_version": 1,
        "slug": slug,
        "phase": "Phase 2",
        "title": "Rollback probe",
        "problem_statement": "The twin must never diverge from the JSON.",
        "tier": "Tier 2",
        "success_criteria": [{"id": "SC1", "criterion": "Rollback restores bytes."}],
        "tasks_skeleton": [{"id": "T1", "title": "Probe", "depends_on": []}],
        "non_goals": [],
        "conceptual_architecture": {
            "overview": "Original overview.",
            "components": [{"name": "engine", "purpose": "Apply ops.", "dependencies": []}],
        },
        "references": [],
    }


@pytest.fixture
def amend_env(tmp_path, monkeypatch):
    """A plan dir with an authored plan, wired so `bin/plan <slug> --amend` runs
    entirely offline: the planner emission is stubbed to a canned patch."""
    from bin._planner import main as planner_main
    from bin._planner.two_call import PlannerResult

    slug = "rollback_probe"
    plan_dir = tmp_path / "docs" / "plans" / slug
    plan_dir.mkdir(parents=True)
    target = plan_dir / f"{slug}_plan.json"
    target.write_text(json.dumps(_valid_plan(slug), indent=2, sort_keys=True) + "\n")
    (plan_dir / f"{slug}_plan.md").write_text("# prior twin\n")

    monkeypatch.setattr(planner_main, "_resolve_plan_dir", lambda _slug: plan_dir)

    patch = {
        "patch_version": 1,
        "ops": [{
            "op_kind": "scalar",
            "action": "replace",
            "address": {"field": "title"},
            "value": "AMENDED TITLE",
        }],
    }

    def _stub_invoke(*_a, **_kw):
        return PlannerResult(
            call1_reasoning_md="(stubbed)",
            call2_emitted_json=patch,
            call1_cost_usd=0.0,
            call2_cost_usd=0.0,
            call1_model_id="stub",
            call2_model_id="stub",
            call1_attempt_count=1,
            call2_attempt_count=1,
        )

    monkeypatch.setattr(planner_main, "invoke_planner", _stub_invoke)
    return {"slug": slug, "plan_dir": plan_dir, "target": target, "main": planner_main}


def test_render_failure_rolls_the_amended_json_back(amend_env, monkeypatch, capsys):
    """The transaction's whole reason to exist: the JSON advanced, the twin did
    not, so NEITHER is left changed."""
    planner_main = amend_env["main"]
    target = amend_env["target"]
    prior = target.read_bytes()

    monkeypatch.setattr(
        planner_main, "_render_md_twin", lambda *_a, **_kw: (True, "forced render failure")
    )

    rc = planner_main.main(["plan", amend_env["slug"], "--amend", "--directive", "retitle it"])

    assert target.read_bytes() == prior, "amended JSON was not rolled back"
    assert rc == exit_codes.EXIT_ATOMIC_WRITE_FAILED, (
        "a rolled-back amend must not report EXIT_OK — the operator's amend did "
        f"not land; got {rc}"
    )
    err = capsys.readouterr().err
    assert "amend_render_failed_rolled_back" in err
    assert '"rolled_back": true' in err.lower().replace("'", '"')


def test_successful_render_leaves_the_amend_applied(amend_env, monkeypatch):
    """The negative control. Without it, a rollback that fired unconditionally
    would pass the test above."""
    planner_main = amend_env["main"]
    target = amend_env["target"]

    monkeypatch.setattr(planner_main, "_render_md_twin", lambda *_a, **_kw: (False, ""))

    rc = planner_main.main(["plan", amend_env["slug"], "--amend", "--directive", "retitle it"])

    assert rc == exit_codes.EXIT_OK
    assert json.loads(target.read_text())["title"] == "AMENDED TITLE"


def test_non_amend_render_failure_does_not_roll_back(amend_env, monkeypatch):
    """Scope guard. The transaction is amend-ONLY by design: `/plan` and
    `/implplan` write fresh authorship, not a surgical patch of a plan that may
    already back an orchestrator, so a render failure there warns and leaves the
    JSON. `_pre_amend_bytes is None` is the structural switch, and this pins that
    the switch still discriminates."""
    planner_main = amend_env["main"]
    monkeypatch.setattr(
        planner_main, "_render_md_twin", lambda *_a, **_kw: (True, "forced render failure")
    )

    # A non-amend run on an existing plan is refused by the create gate long
    # before the write, so the switch is asserted structurally instead: the
    # rollback branch is reached only when pre-amend bytes were captured.
    import inspect

    src = inspect.getsource(planner_main.main)
    assert "if _pre_amend_bytes is not None:" in src, (
        "the amend-only switch guarding the rollback branch has moved or been "
        "removed; re-verify that a non-amend render failure still cannot roll back"
    )
