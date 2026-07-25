"""`/implplan` must not emit an all-stub orchestrator at exit 0.

The defect: `/implplan` could emit an orchestrator whose every task declared no
`file_paths_touched`, no `tests_enabled` and no `test_plan`, write it to disk,
and exit 0. Every per-task rule was individually satisfied — `tests_enabled: []`
is *deliberately* legitimate for bookkeeping/doc tasks and
`_check_tests_enabled_contract` must not false-positive on it — and nothing
looked at the aggregate. The result is a silent no-op success: an artifact that
looks authored, backing a DAG that touches no files and runs no tests, whose
operator's next move is to execute it.

Per-task emptiness is normal and MUST NOT trip the guard; universal emptiness is
the stub-emission shape. The line between those two is what most of this file
pins, because a guard that false-positives on a sparse-but-real orchestrator
would be worse than the defect.

Found by auditing an assumption a downstream agent held ("`/implplan` can emit an
all-empty-stub orchestrator at exit 0 — observed once, unknown whether still
reachable"). It was still reachable on main.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bin._render_plan.exit_codes import EXIT_OK, EXIT_SCHEMA_REJECTED
from bin._render_plan.json_loader import SchemaRejectedError
from bin._verify_plan.strict import (
    DegenerateOrchestratorError,
    run_strict_invariants,
)

@pytest.fixture
def src(tmp_path) -> Path:
    """An orchestrator path with its `plan_ref` sibling actually present.

    `run_strict_invariants` runs EVERY orchestrator invariant, so a fixture that
    skips the plan_ref file makes these tests fail for a reason that has nothing
    to do with the guard under test.
    """
    (tmp_path / "probe_plan.json").write_text("{}")
    return tmp_path / "probe_orchestrator.json"


def _substance_violations(exc: SchemaRejectedError) -> list[dict]:
    """Filter to this guard's records.

    `SchemaRejectedError.__str__` collapses to a violation COUNT, so asserting
    on `str(exc)` tests nothing about which invariant fired.
    """
    return [v for v in exc.violations
            if v.get("validator") == "strict-orchestrator-substance"]


def _task(tid: str, **overrides) -> dict:
    task = {
        "id": tid,
        "title": f"Task {tid}",
        "file_paths_touched": [],
        "tests_enabled": [],
        "agent_assignment": "coder",
    }
    task.update(overrides)
    return task


def _tp(test_id: str) -> dict:
    """A `test_plan` entry in its real shape — a dict, not a string.

    Worth spelling out: `test_plan` is a list of OBJECTS
    (`test_id`/`asserts`/`fixture`), and `_check_verification_kind_markers`
    skips any entry that is not a dict. An earlier draft of this file passed a
    bare string, which silently exercised nothing.
    """
    return {"test_id": test_id, "asserts": "it holds", "fixture": "none"}


def _orch(*tasks: dict, slug: str = "probe") -> dict:
    return {
        "schema_version": 1,
        "slug": slug,
        "phase": "Phase 2",
        "plan_ref": f"{slug}_plan.json",
        "tasks": list(tasks),
    }


def _run(payload: dict, src: Path) -> None:
    run_strict_invariants(payload, "orchestrator", src)


# ---------------------------------------------------------------------------
# The defect.
# ---------------------------------------------------------------------------


def test_all_stub_orchestrator_is_refused(src):
    with pytest.raises(DegenerateOrchestratorError) as exc:
        _run(_orch(_task("T1"), _task("T2"), _task("T3")), src)

    records = _substance_violations(exc.value)
    assert len(records) == 1
    assert "3 tasks" in records[0]["message"]
    payload = exc.value.as_stderr_payload()
    assert payload["error"] == "degenerate_orchestrator_rejected"


def test_the_refusal_is_a_schema_rejection_subclass(src):
    """`bin/verify_plan --strict` and every legacy caller catches the parent.
    Chain mode must keep rejecting the document without any change to it."""
    assert issubclass(DegenerateOrchestratorError, SchemaRejectedError)

    with pytest.raises(SchemaRejectedError):
        _run(_orch(_task("T1")), src)


def test_zero_task_orchestrator_is_refused_not_vacuously_accepted(src):
    """`tasks` is `minItems: 1` in orchestrator_v1, so schema validation gets
    here first today. Pinned anyway: "every task is empty" over an empty list is
    vacuously TRUE, so relaxing that bound without this guard would turn a
    zero-task orchestrator into a silent pass."""
    with pytest.raises(DegenerateOrchestratorError) as exc:
        _run(_orch(), src)
    assert "no tasks at all" in _substance_violations(exc.value)[0]["message"]


# ---------------------------------------------------------------------------
# The false-positive surface. Each of these MUST pass — a guard that refuses a
# legitimate sparse orchestrator is worse than the defect it fixes.
# ---------------------------------------------------------------------------


def test_one_substantive_task_among_stubs_is_accepted(src):
    """The aggregate is the signal. A single real task makes the DAG executable,
    and empty siblings are ordinary."""
    _run(_orch(
        _task("T1"),
        _task("T2", file_paths_touched=["bin/thing.py"]),
        _task("T3"),
    ), src)


def test_files_touched_alone_is_substantive(src):
    """A task that changes files but ships no test selector is sparse, not
    degenerate — that judgement belongs to the tests_enabled contract, not here."""
    _run(_orch(_task("T1", file_paths_touched=["bin/thing.py"])), src)


def test_tests_enabled_alone_is_substantive(src):
    _run(_orch(_task("T1", tests_enabled=["tests/test_thing.py"],
                     file_paths_touched=["tests/test_thing.py"])), src)


def test_a_doc_task_with_a_verification_kind_marker_is_substantive(src):
    """The exemption that makes the guard safe.

    `tests_enabled: []` is legitimate for a doc/bookkeeping task, which declares
    a `verification_kind:` marker in `test_plan` instead. An all-doc-task plan is
    therefore NOT degenerate — every task declares how it will be checked. Had
    the guard keyed only on file_paths_touched and tests_enabled, this shape
    would have been refused, and it is a shape splock explicitly supports.
    """
    _run(_orch(
        _task("T1", test_plan=[_tp("verification_kind: documentation")]),
        _task("T2", test_plan=[_tp("verification_kind: documentation")]),
    ), src)


def test_a_bare_test_plan_is_substantive(src):
    _run(_orch(_task("T1", test_plan=[_tp("manual_cli_check")])), src)


# ---------------------------------------------------------------------------
# Precedence. SC2 makes "the tests_enabled signal is never masked" load-bearing.
# ---------------------------------------------------------------------------


def test_degeneracy_and_the_tests_enabled_contract_cannot_co_occur(src):
    """They are mutually exclusive BY CONSTRUCTION, which is why the precedence
    branch in `run_strict_invariants` is currently unreachable.

    Every way to trip `_check_tests_enabled_contract` requires either a
    non-empty `tests_enabled` or a non-empty `test_plan` — and either one makes
    the task substantive, so the aggregate can never be degenerate at the same
    time. This was discovered by writing the precedence test first and watching
    it fail to raise anything at all.

    The ordering is kept anyway, and it is not dead weight: it becomes reachable
    the moment anyone widens the substance rule (e.g. "a task with no
    file_paths_touched is degenerate regardless of test_plan"). SC2's "the
    distinct plan-defect signal is never masked" would apply immediately, and it
    should not have to be rediscovered then. This test is the tripwire — if it
    ever starts failing, the precedence branch has become live and the ordering
    is doing real work.
    """
    from bin._verify_plan.strict import TestsEnabledContractError

    # A malformed marker: contract violation, and the task is substantive.
    with pytest.raises(TestsEnabledContractError):
        _run(_orch(_task("T1", test_plan=[_tp("verification_kind:")])), src)

    # Prose in tests_enabled: contract violation, and substantive.
    with pytest.raises(TestsEnabledContractError):
        _run(_orch(_task("T1", tests_enabled=["run the tests by hand"])), src)


# ---------------------------------------------------------------------------
# End to end through the real CLI entry point: it must REFUSE, and nothing may
# land on disk.
# ---------------------------------------------------------------------------


@pytest.fixture
def implplan_env(tmp_path, monkeypatch):
    from bin._planner import main as planner_main
    from bin._planner.two_call import PlannerResult

    slug = "stub_probe"
    plan_dir = tmp_path / "docs" / "plans" / slug
    plan_dir.mkdir(parents=True)
    (plan_dir / f"{slug}_plan.json").write_text(json.dumps({"schema_version": 1}))
    monkeypatch.setattr(planner_main, "_resolve_plan_dir", lambda _s: plan_dir)

    def _emit(payload):
        def _stub(*_a, **_kw):
            return PlannerResult(
                call1_reasoning_md="(stubbed)",
                call2_emitted_json=payload,
                call1_cost_usd=0.0,
                call2_cost_usd=0.0,
                call1_model_id="stub",
                call2_model_id="stub",
                call1_attempt_count=1,
                call2_attempt_count=1,
            )

        monkeypatch.setattr(planner_main, "invoke_planner", _stub)

    return {"slug": slug, "plan_dir": plan_dir, "main": planner_main, "emit": _emit,
            "target": plan_dir / f"{slug}_orchestrator.json"}


def test_cli_refuses_an_all_stub_emission_and_writes_nothing(implplan_env, capsys):
    env = implplan_env
    env["emit"](_orch(_task("T1"), _task("T2"), slug=env["slug"]))

    rc = env["main"].main(["implplan", env["slug"]])

    assert rc == EXIT_SCHEMA_REJECTED, f"expected a refusal, got exit {rc}"
    assert not env["target"].exists(), (
        "the rejected orchestrator landed on disk; the pre-write seam must "
        "refuse before the write so 'nothing lands on disk' holds"
    )
    assert "degenerate_orchestrator_rejected" in capsys.readouterr().err


def test_cli_accepts_a_substantive_emission(implplan_env):
    """The negative control. Without it, a guard that refused everything would
    pass the test above."""
    env = implplan_env
    env["emit"](_orch(
        _task("T1", file_paths_touched=["bin/thing.py", "tests/test_thing.py"],
              tests_enabled=["tests/test_thing.py"]),
        slug=env["slug"],
    ))

    rc = env["main"].main(["implplan", env["slug"]])

    assert rc == EXIT_OK, f"a substantive orchestrator was refused: exit {rc}"
    assert env["target"].exists()
