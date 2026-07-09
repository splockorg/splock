"""tests/real_tests_at_junctions/test_validator_reentry_on_rewrite.py

Per `real_tests_at_junctions` T7 test_plan (SC7) — validator RE-ENTRY on
orchestrator rewrite, so prose cannot be re-introduced post-emit:

  1. T7-reopen-rewrite-revalidated  — the `/implplan --reopen` rewrite
     path re-runs the SC2 validator: a prose-bearing regeneration is
     rejected (a) pre-write (the T3 seam, proven on the REOPEN flag
     combination) and (b) post-write on the BYTES THAT ACTUALLY LANDED
     (write-path divergence is detected, the rewrite rolled back).
  2. T7-reserializer-rewrite-revalidated — the operator /tmp
     re-serializer convention's seam
     (`bin/_verify_plan/strict.revalidate_orchestrator_file`) rejects a
     prose-bearing rewrite of the on-disk orchestrator. The re-serializer
     is a CONVENTION, not a fixed code path — the public helper is the
     importable shape the convention binds to (see its docstring).
  3. T7-clean-rewrite-passes        — a selector-only rewrite passes both
     seams without raising.
  4. T7-amend-is-plan-only          — `bin/plan --amend` operates on
     plan_v1 via plan_patch_v1; NEITHER schema carries `tests_enabled`
     (an orchestrator_v1-only field), so amend-coupled re-entry would
     guard an empty path — the amend-side hook is defense-in-depth only.

Synthetic orchestrator payloads throughout (tmp_path fixtures); the
planner SDK is stubbed (`invoke_planner` monkeypatched) — no live LLM
calls; deterministic by construction.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import bin._planner.main as planner_main
from bin._planner import schemas as planner_schemas
from bin._planner.exit_codes import EXIT_OK
from bin._planner.main import _output_target
from bin._planner.two_call import PlannerResult
from bin._render_plan import exit_codes as render_exit_codes
from bin._render_plan.json_loader import (
    JsonMalformedError,
    PlanNotFoundError,
)
from bin._verify_plan.strict import (
    TestsEnabledContractError,
    revalidate_orchestrator_file,
)


_PROSE_ENTRY = (
    "Re-run the whole rewrite end-to-end and verify that prose entries "
    "are rejected by the validator."
)

_CLEAN_TEST_FILE = "tests/t7_demo/test_rewrite.py"
_CLEAN_SELECTOR = f"{_CLEAN_TEST_FILE}::test_rewrite_clean"


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


def _task(tid: str, files: list[str], tests: list) -> dict:
    """Schema-VALID orchestrator task (orchestrator_v1 requires title +
    agent_assignment; `additionalProperties: false`)."""
    return {
        "id": tid,
        "title": f"synthetic task {tid}",
        "file_paths_touched": files,
        "tests_enabled": tests,
        "agent_assignment": {"subagent": "coder", "model": "inherit"},
    }


def _orch(slug: str, tasks: list[dict]) -> dict:
    """Schema-VALID synthetic orchestrator payload (top-level required:
    schema_version, slug, phase, plan_ref, tasks)."""
    return {
        "schema_version": 1,
        "slug": slug,
        "phase": "Phase 3",
        "plan_ref": f"{slug}_plan.json",
        "tasks": tasks,
    }


def _prose_orch(slug: str) -> dict:
    return _orch(slug, [_task("T1", ["src/mod.py"], [_PROSE_ENTRY])])


def _clean_orch(slug: str) -> dict:
    return _orch(slug, [_task("T1", [_CLEAN_TEST_FILE], [_CLEAN_SELECTOR])])


def _seed_plan_dir(tmp_path: Path, slug: str) -> Path:
    """Create docs-plans-shaped dir with the upstream plan substrate
    (implplan requires `<slug>_plan.json`; plan_ref must also resolve so
    the only strict violations in play are the ones each test constructs)."""
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)
    (plan_dir / f"{slug}_plan.json").write_text("{}", encoding="utf-8")
    return plan_dir


def _stub_planner_emitting(payload: dict):
    def _stub_invoke_planner(**kwargs):
        return PlannerResult(
            call1_reasoning_md="(stubbed Call 1 reasoning)",
            call2_emitted_json=payload,
            call1_cost_usd=0.0,
            call2_cost_usd=0.0,
            call1_model_id="(stub)",
            call2_model_id="(stub)",
        )

    return _stub_invoke_planner


# --------------------------------------------------------------------------- #
# 1. T7-reopen-rewrite-revalidated                                             #
# --------------------------------------------------------------------------- #


def test_t7_reopen_rewrite_prose_rejected(tmp_path, monkeypatch, capsys):
    """`bin/implplan --reopen <slug>` regenerating over an EXISTING
    orchestrator re-runs the SC2 validator: a prose-bearing emission is
    rejected with the distinct code and the prior on-disk orchestrator is
    left byte-for-byte UNCHANGED (the rewrite never lands)."""
    slug = "t7_reopen_prose"
    plan_dir = _seed_plan_dir(tmp_path, slug)
    target = plan_dir / f"{slug}_orchestrator.json"
    prior_text = json.dumps(_clean_orch(slug), indent=2) + "\n"
    target.write_text(prior_text, encoding="utf-8")

    monkeypatch.setattr(planner_main, "_PLANS_DIR", tmp_path)
    monkeypatch.setattr(
        planner_main, "invoke_planner", _stub_planner_emitting(_prose_orch(slug))
    )

    rc = planner_main.main(["implplan", "--reopen", slug])

    assert rc == render_exit_codes.EXIT_TESTS_ENABLED_REJECTED
    # The rewrite was rejected: prior orchestrator untouched, prose never
    # re-introduced.
    assert target.read_text(encoding="utf-8") == prior_text
    # No derived side-effects from the rejected rewrite.
    assert not (plan_dir / f"{slug}_orchestrator.md").exists()

    err = capsys.readouterr().err
    assert "tests_enabled_contract_rejected" in err
    # A rejected rewrite must not announce a destructive overwrite.
    assert "Reopened:" not in err


def test_t7_reopen_rewrite_post_write_bytes_revalidated_and_rolled_back(
    tmp_path, monkeypatch, capsys
):
    """The re-entry seam validates the BYTES THAT ACTUALLY LANDED, not just
    the in-memory payload: a write-path divergence that lands a
    prose-bearing orchestrator (while the validated payload was clean) is
    caught post-write, the rewrite is ROLLED BACK to the pre-rewrite
    bytes, and the distinct code is returned."""
    slug = "t7_reopen_diverge"
    plan_dir = _seed_plan_dir(tmp_path, slug)
    target = plan_dir / f"{slug}_orchestrator.json"
    prior_text = json.dumps(_clean_orch(slug), indent=2) + "\n"
    target.write_text(prior_text, encoding="utf-8")

    # The EMITTED payload is clean — it passes the pre-write T3 seam...
    monkeypatch.setattr(planner_main, "_PLANS_DIR", tmp_path)
    monkeypatch.setattr(
        planner_main, "invoke_planner", _stub_planner_emitting(_clean_orch(slug))
    )

    # ...but the write path diverges and lands PROSE on disk instead.
    prose_text = json.dumps(_prose_orch(slug), indent=2) + "\n"

    def _divergent_write(target_path: Path, payload: dict) -> None:
        target_path.write_text(prose_text, encoding="utf-8")

    monkeypatch.setattr(planner_main, "_write_output", _divergent_write)

    rc = planner_main.main(["implplan", "--reopen", slug])

    assert rc == render_exit_codes.EXIT_TESTS_ENABLED_REJECTED
    # Rollback: the pre-rewrite bytes were restored verbatim.
    assert target.read_text(encoding="utf-8") == prior_text

    err = capsys.readouterr().err
    assert "tests_enabled_contract_rejected" in err
    assert "post_write_rewrite_revalidation" in err
    assert '"rewrite_undone": true' in err
    assert "Reopened:" not in err


# --------------------------------------------------------------------------- #
# 2. T7-reserializer-rewrite-revalidated                                       #
# --------------------------------------------------------------------------- #


def test_t7_reserializer_rewrite_prose_rejected(tmp_path):
    """The operator /tmp re-serializer convention's post-write seam: a
    re-serialized orchestrator that re-introduces a prose tests_enabled
    entry is rejected by `revalidate_orchestrator_file` with the DISTINCT
    contract error naming the offending task + entry."""
    slug = "t7_reserialize_prose"
    plan_dir = _seed_plan_dir(tmp_path, slug)
    orch_path = plan_dir / f"{slug}_orchestrator.json"
    # Simulate the re-serializer's write (Python I/O, hook-free) of a
    # schema-valid document whose only defect is the prose entry.
    orch_path.write_text(json.dumps(_prose_orch(slug)), encoding="utf-8")

    with pytest.raises(TestsEnabledContractError) as ei:
        revalidate_orchestrator_file(orch_path)

    exc = ei.value
    assert exc.as_stderr_payload()["error"] == "tests_enabled_contract_rejected"
    violation = exc.violations[0]
    assert violation["validator"] == "strict-tests-enabled-contract"
    assert "T1" in violation["message"]
    assert _PROSE_ENTRY in violation["message"]


def test_t7_reserializer_contract_signal_not_masked_by_schema_invalidity(
    tmp_path,
):
    """Strict-first ordering inside the re-entry seam: a rewrite that is
    BOTH schema-invalid (missing required `phase`) and prose-bearing still
    raises the DISTINCT TestsEnabledContractError — the plan-defect signal
    is never masked by a co-occurring generic schema violation."""
    slug = "t7_reserialize_masked"
    plan_dir = _seed_plan_dir(tmp_path, slug)
    orch_path = plan_dir / f"{slug}_orchestrator.json"
    doc = _prose_orch(slug)
    del doc["phase"]  # schema-invalid
    orch_path.write_text(json.dumps(doc), encoding="utf-8")

    with pytest.raises(TestsEnabledContractError):
        revalidate_orchestrator_file(orch_path)


def test_t7_reserializer_rejects_unparseable_rewrite(tmp_path):
    """A re-serializer write that leaves unparseable JSON (or no file at
    all) raises loudly — any exception from the seam means 'the rewrite is
    rejected; restore the prior bytes'."""
    slug = "t7_reserialize_broken"
    plan_dir = _seed_plan_dir(tmp_path, slug)
    orch_path = plan_dir / f"{slug}_orchestrator.json"

    with pytest.raises(PlanNotFoundError):
        revalidate_orchestrator_file(orch_path)  # nothing written

    orch_path.write_text('{"schema_version": 1, "tasks": [', encoding="utf-8")
    with pytest.raises(JsonMalformedError):
        revalidate_orchestrator_file(orch_path)


# --------------------------------------------------------------------------- #
# 3. T7-clean-rewrite-passes                                                   #
# --------------------------------------------------------------------------- #


def test_t7_clean_rewrite_passes_reserializer(tmp_path):
    """A selector-only re-serialized orchestrator passes re-validation —
    the seam returns the validated payload (no raise)."""
    slug = "t7_reserialize_clean"
    plan_dir = _seed_plan_dir(tmp_path, slug)
    orch_path = plan_dir / f"{slug}_orchestrator.json"
    orch_path.write_text(json.dumps(_clean_orch(slug)), encoding="utf-8")

    payload = revalidate_orchestrator_file(orch_path)  # must not raise
    assert payload["slug"] == slug
    assert payload["tasks"][0]["tests_enabled"] == [_CLEAN_SELECTOR]


def test_t7_clean_rewrite_passes_end_to_end(tmp_path, monkeypatch, capsys):
    """`bin/implplan --reopen` with a selector-only regeneration passes
    both the pre-write and post-write seams: exit 0, target overwritten
    with the clean orchestrator, overwrite notice fires, no contract
    rejection on stderr."""
    slug = "t7_reopen_clean"
    plan_dir = _seed_plan_dir(tmp_path, slug)
    target = plan_dir / f"{slug}_orchestrator.json"
    target.write_text('{"prior": "orch"}\n', encoding="utf-8")

    monkeypatch.setattr(planner_main, "_PLANS_DIR", tmp_path)
    monkeypatch.setattr(
        planner_main, "invoke_planner", _stub_planner_emitting(_clean_orch(slug))
    )

    rc = planner_main.main(["implplan", "--reopen", slug])

    assert rc == EXIT_OK
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["slug"] == slug  # stamped
    assert written["tasks"][0]["tests_enabled"] == [_CLEAN_SELECTOR]

    err = capsys.readouterr().err
    assert "tests_enabled_contract_rejected" not in err
    assert f"Reopened: overwrote {target.resolve()}" in err


def test_t7_clean_orchestrator_passes_reentry_seam(tmp_path):
    """No false positives on the re-entry seam.

    Upstream this dogfoods the source repo's own closed orchestrator. That
    artifact is that repo's history rather than framework code, so the pin is
    reconstructed against a clean synthetic orchestrator: parse + strict
    invariants + full JSON Schema must all pass without raising.
    """
    slug = "synthetic_reentry"
    plan_dir = _seed_plan_dir(tmp_path, slug)
    target = plan_dir / f"{slug}_orchestrator.json"
    target.write_text(json.dumps(_clean_orch(slug)), encoding="utf-8")

    payload = revalidate_orchestrator_file(target)  # must not raise
    assert payload["slug"] == slug


# --------------------------------------------------------------------------- #
# 4. T7-amend-is-plan-only                                                     #
# --------------------------------------------------------------------------- #


def _contains_key(node, key: str) -> bool:
    """Recursive containment check for a dict key anywhere in a JSON tree."""
    if isinstance(node, dict):
        if key in node:
            return True
        return any(_contains_key(v, key) for v in node.values())
    if isinstance(node, list):
        return any(_contains_key(item, key) for item in node)
    return False


def test_t7_amend_is_plan_only(tmp_path):
    """`bin/plan --amend` is plan-only by construction (the recon re-run
    finding behind SC7's re-aim): the amend Call-2 emits a plan_patch_v1
    patch applied to plan_v1, and NEITHER schema carries `tests_enabled`
    — that field exists only in orchestrator_v1. An amend therefore
    cannot re-introduce prose into tests_enabled; the amend-side hook is
    defense-in-depth only, and the real re-entry triggers are the
    orchestrator rewrite paths covered above."""
    # The exact schema constants the planner's Call 2 binds (two_call.py
    # selects PLAN_PATCH_SCHEMA_V1 for step == "amend"; patch_apply
    # re-validates the amended plan with kind="plan" → plan_v1).
    assert not _contains_key(planner_schemas.PLAN_SCHEMA_V1, "tests_enabled")
    assert not _contains_key(
        planner_schemas.PLAN_PATCH_SCHEMA_V1, "tests_enabled"
    )
    # Contrast pin: tests_enabled IS an orchestrator_v1 field — proving the
    # absence above is meaningful, not a renamed field.
    assert _contains_key(planner_schemas.IMPLPLAN_SCHEMA_V1, "tests_enabled")

    # The amend carrier step is "plan" (a flag on the plan subcommand):
    # its output target is `<slug>_plan.json` — an amend NEVER writes the
    # orchestrator, so orchestrator re-entry coupled to amend would guard
    # an empty path.
    slug = "t7_amend_target"
    target = _output_target(tmp_path / slug, slug, "plan")
    assert target.name == f"{slug}_plan.json"
    assert not target.name.endswith("_orchestrator.json")
