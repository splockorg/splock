"""tests/real_tests_at_junctions/test_strict_tests_enabled_validator.py

Per `real_tests_at_junctions` T3 test_plan (SC2) — the deterministic
plan-time tests_enabled validator in
`bin/_verify_plan/strict.py::run_strict_invariants`:

  1. T3-rejects-prose            — prose entry → DISTINCT plan-defect signal
                                   naming the offending task + entry.
  2. T3-accepts-selector-in-file_paths — selector whose path is in some
                                   task's file_paths_touched passes.
  3. T3-rejects-phantom-selector — selector path in NO task's
                                   file_paths_touched is rejected.
  4. T3-allows-selector-to-be    — path-membership NOT is_file(): selector
                                   for a not-yet-authored file passes.
  5. T3-no-false-positive-empty  — `tests_enabled: []` passes.
  6. T3-authored-typed-command-rejected — `gate_cmd:` entry REJECTED.
                                   FLIPPED from the original T3
                                   allowance (T3-no-false-positive-
                                   typed-command) by the post-T8
                                   operator-approved follow-up patch:
                                   the prefix is RESERVED do-not-author
                                   under the narrowed SC3 branch
                                   (typed_gate_command_decision.md §4).
  7. T3-distinct-exit-code-mapping — plan-defect exit code distinct from
                                   EXIT_USAGE; chain-overnight maps it to
                                   its OWN verdict (not collapsed to 16).
  8. T3-operator-direct-coverage — the /implplan emission path in
                                   `bin/_planner/main.py` rejects a
                                   prose-bearing orchestrator (chain mode
                                   is no longer the only validated path).

Plus the post-T8 follow-up-patch section (strict-validator hardening):
the `verification_kind:` near-miss lint (misspelled markers reject with
a did-you-mean instead of silently degrading to bookkeeping entries) and
the duplicate-marker coherence rejection.

Plus the dogfood regression pin: this slug's own real orchestrator
(8 selector tasks + T9 `[]`) passes both `run_strict_invariants` and the
end-to-end `bin/verify_plan --strict` dispatch.

Synthetic orchestrator dicts throughout; no SDK calls (fixture 8 stubs
`invoke_planner`); deterministic by construction.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bin._render_plan import exit_codes as render_exit_codes
from bin._render_plan.json_loader import SchemaRejectedError
from bin._chain_overnight import exit_codes as chain_exit_codes
from bin._chain_overnight import state_machine
from bin._verify_plan.strict import (
    TYPED_GATE_COMMAND_PREFIX,
    VERIFICATION_KIND_MARKER_PREFIX,
    TestsEnabledContractError,
    run_strict_invariants,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

_SYNTHETIC_SOURCE = Path("/nonexistent/synthetic_orchestrator.json")

_PROSE_ENTRY = (
    "Write unit tests covering prose rejection. Also verify that phantom "
    "selectors are caught and reported."
)


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


def _task(tid: str, files: list[str], tests: list) -> dict:
    return {"id": tid, "file_paths_touched": files, "tests_enabled": tests}


def _orch(tasks: list[dict]) -> dict:
    """Minimal synthetic orchestrator payload for `run_strict_invariants`.

    `plan_ref` is deliberately absent (falsy) so `_check_plan_ref_exists`
    is a no-op and the only violations in play are the ones each fixture
    constructs.
    """
    return {"schema_version": 1, "slug": "synthetic", "tasks": tasks}


def _run(payload: dict) -> None:
    run_strict_invariants(payload, "orchestrator", _SYNTHETIC_SOURCE)


# --------------------------------------------------------------------------- #
# 1. T3-rejects-prose                                                          #
# --------------------------------------------------------------------------- #


def test_t3_rejects_prose():
    """A multi-sentence prose entry raises the DISTINCT contract error,
    with a diagnostic naming the offending task and the entry verbatim."""
    payload = _orch(
        [_task("T1", ["bin/_verify_plan/strict.py"], [_PROSE_ENTRY])]
    )

    with pytest.raises(TestsEnabledContractError) as ei:
        _run(payload)

    exc = ei.value
    # Distinct signal: the subclass, not the plain schema-rejection class.
    assert isinstance(exc, SchemaRejectedError)
    assert len(exc.violations) == 1
    violation = exc.violations[0]
    assert violation["validator"] == "strict-tests-enabled-contract"
    # Diagnostic names the offending task + the entry verbatim.
    assert "T1" in violation["message"]
    assert _PROSE_ENTRY in violation["message"]
    # The stderr envelope carries the distinct error discriminator.
    assert exc.as_stderr_payload()["error"] == "tests_enabled_contract_rejected"


# --------------------------------------------------------------------------- #
# 2. T3-accepts-selector-in-file_paths                                         #
# --------------------------------------------------------------------------- #


def test_t3_accepts_selector_in_file_paths():
    """`tests/x.py::test_y` passes when `tests/x.py` is in some task's
    file_paths_touched (same task here; cross-task in the next assert)."""
    payload = _orch(
        [_task("T1", ["src/mod.py", "tests/x.py"], ["tests/x.py::test_y"])]
    )
    _run(payload)  # must not raise

    # Plan SC2 wording binds across the plan ("any task's
    # file_paths_touched"), so a selector authored by a SIBLING task's
    # file also passes.
    payload = _orch(
        [
            _task("T1", ["tests/x.py"], []),
            _task("T2", ["src/mod.py"], ["tests/x.py::test_y"]),
        ]
    )
    _run(payload)  # must not raise


def test_t3_accepts_parametrized_node_id_with_whitespace_in_brackets():
    """Whitespace is checked on the path component only — parametrized
    node-IDs with spaces inside `[...]` are valid selectors (sdk_spawners
    heuristic parity)."""
    payload = _orch(
        [
            _task(
                "T1",
                ["tests/x.py"],
                ["tests/x.py::test_y[case a-case b]"],
            )
        ]
    )
    _run(payload)  # must not raise


# --------------------------------------------------------------------------- #
# 3. T3-rejects-phantom-selector                                               #
# --------------------------------------------------------------------------- #


def test_t3_rejects_phantom_selector():
    """A selector whose path component is in NO task's file_paths_touched
    is a phantom and is rejected."""
    payload = _orch(
        [
            _task("T1", ["tests/x.py"], []),
            _task("T2", ["src/mod.py"], ["tests/ghost.py::test_z"]),
        ]
    )

    with pytest.raises(TestsEnabledContractError) as ei:
        _run(payload)

    violation = ei.value.violations[0]
    assert violation["validator"] == "strict-tests-enabled-contract"
    assert "phantom" in violation["message"]
    assert "T2" in violation["message"]
    assert "tests/ghost.py::test_z" in violation["message"]


# --------------------------------------------------------------------------- #
# 4. T3-allows-selector-to-be                                                  #
# --------------------------------------------------------------------------- #


def test_t3_allows_selector_to_be():
    """Path-membership, NOT is_file(): a selector for a file that does not
    exist on disk but IS in file_paths_touched passes (files do not exist
    until the task runs)."""
    to_be = "tests/real_tests_at_junctions/_selector_to_be_not_authored_yet.py"
    assert not (_REPO_ROOT / to_be).exists(), (
        "fixture precondition: the selector-to-be file must NOT exist on "
        "disk for this test to prove the is_file()-free membership check"
    )
    payload = _orch([_task("T1", [to_be], [f"{to_be}::test_future"])])
    _run(payload)  # must not raise


# --------------------------------------------------------------------------- #
# 5. T3-no-false-positive-empty                                                #
# --------------------------------------------------------------------------- #


def test_t3_no_false_positive_empty():
    """`tests_enabled: []` (bookkeeping / doc-only tasks) must not trigger
    rejection."""
    payload = _orch(
        [
            _task("T1", ["docs/notes.md"], []),
            _task("T2", [], []),
        ]
    )
    _run(payload)  # must not raise


# --------------------------------------------------------------------------- #
# 6. T3-authored-typed-command-rejected                                        #
# --------------------------------------------------------------------------- #


def test_t3_authored_typed_command_rejected():
    """FLIPPED from T3-no-false-positive-typed-command by the post-T8
    operator-approved follow-up patch: an authored typed gate command is
    a contract violation — the prefix is RESERVED do-not-author under the
    narrowed SC3 branch, and the old allowance let an authored entry
    vacuously count as advance-ok junction credit
    (typed_gate_command_decision.md §4: authoring becomes legal only on
    the GENERALIZE flip)."""
    typed = f"{TYPED_GATE_COMMAND_PREFIX} python -m bin.cli_lint --check"
    payload = _orch([_task("T1", ["bin/some_heredoc_target"], [typed])])

    with pytest.raises(TestsEnabledContractError) as ei:
        _run(payload)

    violation = ei.value.violations[0]
    assert violation["validator"] == "strict-tests-enabled-contract"
    assert "T1" in violation["message"]
    assert typed in violation["message"]
    assert "RESERVED" in violation["message"]
    assert "must NOT be authored" in violation["message"]
    assert (
        "docs/plans/_closed/real_tests_at_junctions/typed_gate_command_decision.md §4"
        in violation["message"]
    )

    # The prefix constant is the single source T6 (SC3) imports; pin its
    # exported value so a silent rename breaks loudly.
    assert TYPED_GATE_COMMAND_PREFIX == "gate_cmd:"


# --------------------------------------------------------------------------- #
# 7. T3-distinct-exit-code-mapping                                             #
# --------------------------------------------------------------------------- #


def test_t3_distinct_exit_code_mapping():
    """The plan-defect exit code is distinct from EXIT_USAGE (and from the
    generic schema-rejection code) and maps through chain-overnight to its
    OWN verdict — not collapsed silently into 16."""
    code = render_exit_codes.EXIT_TESTS_ENABLED_REJECTED

    # render-plan registry: distinct constant, member of the closed enum.
    assert code != render_exit_codes.EXIT_USAGE
    assert code != render_exit_codes.EXIT_SCHEMA_REJECTED
    assert code in render_exit_codes.ALL_CODES

    # chain-overnight mirror: same number, same name (verbatim
    # propagation, like the code-7 atomic-write family).
    assert chain_exit_codes.EXIT_TESTS_ENABLED_REJECTED == code

    # The chain mapping-table entry routes 44 to its own constant, NOT to
    # the generic EXIT_VERIFY_PLAN_REJECTED (16) the other render-plan
    # rejections (3/4/5/6/11) collapse into.
    mapped = chain_exit_codes.PROPAGATED_FROM_VERIFY_PLAN[code]
    assert mapped == chain_exit_codes.EXIT_TESTS_ENABLED_REJECTED
    assert mapped != chain_exit_codes.EXIT_VERIFY_PLAN_REJECTED
    assert (
        chain_exit_codes.PROPAGATED_FROM_VERIFY_PLAN[
            render_exit_codes.EXIT_SCHEMA_REJECTED
        ]
        == chain_exit_codes.EXIT_VERIFY_PLAN_REJECTED
    )

    # Verdict seam: its own verdict string, mapped to 7-status `blocked`.
    assert state_machine.verdict_for_verify_plan_exit(code) == (
        "tests_enabled_rejected"
    )
    assert state_machine.verdict_for_verify_plan_exit(
        render_exit_codes.EXIT_SCHEMA_REJECTED
    ) == "verify_plan_rejected"
    assert state_machine._VERDICT_STATUS["tests_enabled_rejected"] == "blocked"
    assert (
        state_machine._HALT_REASON_STATUS["tests_enabled_rejected"] == "blocked"
    )


def test_t3_verify_dispatch_returns_distinct_code(tmp_path):
    """End-to-end through `bin/verify_plan --strict` dispatch: a written
    prose-bearing orchestrator exits with the DISTINCT code, not
    EXIT_SCHEMA_REJECTED."""
    from bin._render_plan.verify import main as verify_main

    slug = "synthetic_prose"
    plan_dir = tmp_path / slug
    plan_dir.mkdir()
    # plan_ref must resolve so the ONLY violation is the contract one.
    (plan_dir / f"{slug}_plan.json").write_text("{}", encoding="utf-8")
    orch_path = plan_dir / f"{slug}_orchestrator.json"
    # Schema-VALID document (the dispatch runs JSON Schema validation
    # before the strict invariants) whose only defect is the prose entry.
    task = _task("T1", ["src/mod.py"], [_PROSE_ENTRY])
    task["title"] = "synthetic prose-bearing task"
    task["agent_assignment"] = {"subagent": "coder", "model": "inherit"}
    orch_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "slug": slug,
                "phase": "Phase 3",
                "plan_ref": f"{slug}_plan.json",
                "tasks": [task],
            }
        ),
        encoding="utf-8",
    )

    rc = verify_main([str(orch_path), "--strict"])
    assert rc == render_exit_codes.EXIT_TESTS_ENABLED_REJECTED


# --------------------------------------------------------------------------- #
# 8. T3-operator-direct-coverage                                               #
# --------------------------------------------------------------------------- #


def test_t3_operator_direct_coverage(tmp_path, monkeypatch, capsys):
    """The operator-direct /implplan emission path in `bin/_planner/main.py`
    invokes the validator (not only chain mode): a stubbed Call-2 emission
    carrying a prose tests_enabled entry is rejected with the distinct
    code BEFORE the orchestrator lands on disk."""
    import bin._planner.main as planner_main
    from bin._planner.two_call import PlannerResult

    slug = "operator_direct_demo"
    plan_dir = tmp_path / slug
    plan_dir.mkdir()
    # implplan requires the upstream plan substrate to exist.
    (plan_dir / f"{slug}_plan.json").write_text("{}", encoding="utf-8")

    prose_orchestrator = {
        "schema_version": 1,
        "slug": slug,
        "tasks": [
            _task("T1", ["src/mod.py"], [_PROSE_ENTRY]),
        ],
    }

    def _stub_invoke_planner(**kwargs):
        return PlannerResult(
            call1_reasoning_md="(stubbed Call 1 reasoning)",
            call2_emitted_json=prose_orchestrator,
            call1_cost_usd=0.0,
            call2_cost_usd=0.0,
            call1_model_id="(stub)",
            call2_model_id="(stub)",
        )

    monkeypatch.setattr(planner_main, "_PLANS_DIR", tmp_path)
    monkeypatch.setattr(planner_main, "invoke_planner", _stub_invoke_planner)

    rc = planner_main.main(["implplan", slug])

    assert rc == render_exit_codes.EXIT_TESTS_ENABLED_REJECTED
    # Emission failed loudly BEFORE the write: no orchestrator on disk.
    assert not (plan_dir / f"{slug}_orchestrator.json").exists()
    # And no MD twin / Call-1 reasoning side-effects either.
    assert not (plan_dir / f"{slug}_orchestrator.md").exists()

    err = capsys.readouterr().err
    assert "tests_enabled_contract_rejected" in err
    assert "T1" in err


# --------------------------------------------------------------------------- #
# follow-up patch: verification_kind near-miss lint + marker coherence        #
# --------------------------------------------------------------------------- #

# The five near-miss spellings the follow-up patch requires the lint to
# catch (each silently degraded to a plain bookkeeping entry before).
_NEAR_MISS_SPELLINGS = [
    "verification-kind:",
    "Verification_kind:",
    " verification_kind:",
    "verification_kind :",
    "verification_kinds:",
]


def _task_with_plan(
    tid: str, files: list[str], tests: list, test_ids: list[str]
) -> dict:
    """`_task` plus a test_plan built from raw test_id strings (the
    lint's input surface)."""
    task = _task(tid, files, tests)
    task["test_plan"] = [
        {
            "test_id": test_id,
            "asserts": "what the non-pytest verification establishes",
            "fixture": "doc-review",
        }
        for test_id in test_ids
    ]
    return task


@pytest.mark.parametrize("spelling", _NEAR_MISS_SPELLINGS)
def test_near_miss_marker_spelling_rejected(spelling):
    """Each near-miss spelling raises the DISTINCT contract error with a
    did-you-mean diagnostic naming the exact prefix — a misspelled marker
    must not silently degrade AND evade the coherence checks."""
    payload = _orch(
        [
            _task_with_plan(
                "T1", ["docs/some_doc.md"], [], [f"{spelling} artifact_review"]
            )
        ]
    )

    with pytest.raises(TestsEnabledContractError) as ei:
        _run(payload)

    violation = ei.value.violations[0]
    assert violation["validator"] == "strict-tests-enabled-contract"
    assert "T1" in violation["message"]
    assert "did you mean" in violation["message"]
    assert VERIFICATION_KIND_MARKER_PREFIX in violation["message"]


def test_exact_marker_still_accepted():
    """The exact well-formed prefix passes through to the existing kind
    validation unchanged — the lint must not catch the real marker (the
    T9-style `[]` + 'verification_kind: artifact_review' shape)."""
    payload = _orch(
        [
            _task_with_plan(
                "T1",
                ["docs/some_doc.md"],
                [],
                [f"{VERIFICATION_KIND_MARKER_PREFIX} artifact_review"],
            )
        ]
    )
    _run(payload)  # must not raise


def test_bare_empty_without_marker_still_accepted_with_ordinary_test_plan():
    """A bare `tests_enabled: []` task whose test_plan carries ORDINARY
    test_ids passes — the lint is anchored and must not false-positive
    on descriptive ids that merely mention the words mid-string."""
    payload = _orch(
        [
            _task_with_plan(
                "T1",
                ["docs/notes.md"],
                [],
                ["T1-ordinary-test-plan-id", "T1-verification-kind-coverage"],
            )
        ]
    )
    _run(payload)  # must not raise


def test_duplicate_exact_markers_rejected():
    """Two exact-prefix markers on one task are rejected — the old
    first-wins resolution was silent (coherence rule folded under the
    follow-up lint umbrella)."""
    payload = _orch(
        [
            _task_with_plan(
                "T1",
                ["docs/some_doc.md"],
                [],
                [
                    f"{VERIFICATION_KIND_MARKER_PREFIX} artifact_review",
                    f"{VERIFICATION_KIND_MARKER_PREFIX} doc_review",
                ],
            )
        ]
    )

    with pytest.raises(TestsEnabledContractError) as ei:
        _run(payload)

    violation = ei.value.violations[0]
    assert violation["validator"] == "strict-tests-enabled-contract"
    assert "T1" in violation["message"]
    assert "at most ONE" in violation["message"]


def test_empty_kind_marker_still_rejected():
    """Regression re-pin: the pre-existing malformed (empty-kind) marker
    rejection still fires alongside the new lint paths."""
    payload = _orch(
        [
            _task_with_plan(
                "T1",
                ["docs/some_doc.md"],
                [],
                [f"{VERIFICATION_KIND_MARKER_PREFIX}   "],
            )
        ]
    )
    with pytest.raises(TestsEnabledContractError) as ei:
        _run(payload)
    assert "malformed" in ei.value.violations[0]["message"]


def test_marker_contradiction_still_rejected():
    """Regression re-pin: a marker co-occurring with non-empty
    tests_enabled stays contradictory."""
    payload = _orch(
        [
            _task_with_plan(
                "T1",
                ["tests/x.py"],
                ["tests/x.py::test_y"],
                [f"{VERIFICATION_KIND_MARKER_PREFIX} artifact_review"],
            )
        ]
    )
    with pytest.raises(TestsEnabledContractError) as ei:
        _run(payload)
    messages = " | ".join(v["message"] for v in ei.value.violations)
    assert "contradictory" in messages


# --------------------------------------------------------------------------- #
# precedence + generic-path regression pins                                    #
# --------------------------------------------------------------------------- #


def test_generic_only_violations_keep_plain_schema_rejected():
    """A document with ONLY generic strict violations (duplicate task ids)
    raises the plain SchemaRejectedError — the distinct contract error is
    reserved for tests_enabled defects."""
    payload = _orch(
        [
            _task("T1", ["src/a.py"], []),
            _task("T1", ["src/b.py"], []),
        ]
    )
    with pytest.raises(SchemaRejectedError) as ei:
        _run(payload)
    assert not isinstance(ei.value, TestsEnabledContractError)


def test_contract_violation_takes_precedence_over_generic():
    """When a contract violation co-occurs with a generic violation, the
    DISTINCT error is raised (carrying all violations) so the plan-defect
    signal is never masked."""
    payload = _orch(
        [
            _task("T1", ["src/a.py"], [_PROSE_ENTRY]),
            _task("T1", ["src/b.py"], []),  # duplicate id → generic
        ]
    )
    with pytest.raises(TestsEnabledContractError) as ei:
        _run(payload)
    validators = {v["validator"] for v in ei.value.violations}
    assert "strict-tests-enabled-contract" in validators
    assert "strict-unique-ids" in validators


# --------------------------------------------------------------------------- #
# no-false-positive pins: a realistic, multi-task orchestrator                  #
#                                                                               #
# Upstream these two dogfood the source repo's OWN closed plan artifact         #
# (`docs/plans/_closed/real_tests_at_junctions/…_orchestrator.json`). That       #
# artifact is that repo's history, not framework code — it is not carried here,  #
# and rewriting a closed plan's record to make it portable would be worse than   #
# dropping it. The assertion the dogfood actually bought was "a realistic,       #
# full-size document does NOT trip the upgraded validator", so it is preserved   #
# below against a synthetic orchestrator of the same shape: several selector-    #
# bearing tasks plus one deliberately-empty `tests_enabled`.                     #
# --------------------------------------------------------------------------- #


def _realistic_tasks() -> list[dict]:
    """8 selector-bearing tasks + 1 with an empty `tests_enabled`.

    Mirrors the shape of a real emitted orchestrator: each task authors both its
    implementation and the test file its selector names, so the phantom-selector
    check has real work to do and must still find nothing.
    """
    tasks = [
        _task(
            f"T{i}",
            [f"bin/_mod_{i}/impl.py", f"tests/test_mod_{i}.py"],
            [f"tests/test_mod_{i}.py::test_behavior"],
        )
        for i in range(1, 9)
    ]
    # The trailing `[]` task: legal, and the case a naive "every task must
    # carry a selector" rule would wrongly reject.
    tasks.append(_task("T9", ["docs/notes.md"], []))
    return tasks


def test_realistic_orchestrator_passes_strict_invariants():
    """No false positives: a full-size, well-formed orchestrator must not raise."""
    run_strict_invariants(_orch(_realistic_tasks()), "orchestrator", _SYNTHETIC_SOURCE)


def test_realistic_orchestrator_passes_verify_strict_end_to_end(tmp_path):
    """And the accept path end-to-end through `bin/verify_plan --strict`.

    The reject path is pinned by `test_t3_verify_dispatch_returns_distinct_code`;
    this is its counterpart, so the dispatch is covered in both directions.
    """
    from bin._render_plan.verify import main as verify_main

    slug = "synthetic_clean"
    plan_dir = tmp_path / slug
    plan_dir.mkdir()
    (plan_dir / f"{slug}_plan.json").write_text("{}", encoding="utf-8")
    orch_path = plan_dir / f"{slug}_orchestrator.json"

    tasks = _realistic_tasks()
    for task in tasks:
        task["title"] = f"task {task['id']}"
        task["agent_assignment"] = {"subagent": "coder", "model": "inherit"}
    orch_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "slug": slug,
                "phase": "Phase 3",
                "plan_ref": f"{slug}_plan.json",
                "tasks": tasks,
            }
        ),
        encoding="utf-8",
    )

    assert verify_main([str(orch_path), "--strict"]) == render_exit_codes.EXIT_OK
