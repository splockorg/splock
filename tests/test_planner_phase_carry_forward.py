"""F5 regression: implplan carries the plan's ``phase`` onto the orchestrator,
and the widened orchestrator schema accepts the carried value.

Before F5, ``plan_v1`` pinned ``phase`` to ``["Phase 2"]`` and ``orchestrator_v1``
pinned it to ``["Phase 3"]`` as *independent* single-value enums, so promoting a
plan produced an orchestrator that disagreed with the plan it came from (the
Call-2 emission is schema-forced to "Phase 3"). F5 stamps ``plan.phase`` over the
emitted value and widens the orchestrator enum so the carried value survives the
render/verify re-validation gate.
"""

from __future__ import annotations

import json

import pytest

from bin._planner.main import _carry_forward_plan_phase
from bin._render_plan.json_loader import (
    SchemaRejectedError,
    validate_against_schema,
)


# --------------------------------------------------------------------------- #
# Part A — the pure carry-forward helper
# --------------------------------------------------------------------------- #

def test_carry_forward_copies_plan_phase():
    orch = {"phase": "Phase 3", "slug": "demo"}
    _carry_forward_plan_phase(orch, json.dumps({"phase": "Phase 2"}))
    assert orch["phase"] == "Phase 2"


def test_carry_forward_copies_arbitrary_value():
    # The helper is value-agnostic — whatever the plan carries wins.
    orch = {"phase": "Phase 3"}
    _carry_forward_plan_phase(orch, json.dumps({"phase": "Whatever the plan says"}))
    assert orch["phase"] == "Whatever the plan says"


def test_noop_when_prior_plan_none():
    orch = {"phase": "Phase 3"}
    _carry_forward_plan_phase(orch, None)
    assert orch["phase"] == "Phase 3"


def test_noop_when_prior_plan_empty():
    orch = {"phase": "Phase 3"}
    _carry_forward_plan_phase(orch, "")
    assert orch["phase"] == "Phase 3"


def test_noop_when_prior_plan_omits_phase():
    orch = {"phase": "Phase 3"}
    _carry_forward_plan_phase(orch, json.dumps({"slug": "demo"}))
    assert orch["phase"] == "Phase 3"


def test_noop_when_prior_plan_unparseable():
    orch = {"phase": "Phase 3"}
    _carry_forward_plan_phase(orch, "{ not valid json")
    assert orch["phase"] == "Phase 3"


def test_noop_when_orchestrator_not_dict():
    # Must not raise on a non-dict payload (defensive; mirrors the slug stamp gate).
    _carry_forward_plan_phase(None, json.dumps({"phase": "Phase 2"}))
    _carry_forward_plan_phase("not-a-dict", json.dumps({"phase": "Phase 2"}))


# --------------------------------------------------------------------------- #
# Part B — the widened orchestrator schema accepts the carried value at the
# render/verify re-validation gate (the piece a main.py-only fix would break)
# --------------------------------------------------------------------------- #

def _minimal_orchestrator(phase: str) -> dict:
    return {
        "schema_version": 1,
        "slug": "demo_slug",
        "phase": phase,
        "plan_ref": "demo_slug_plan.json",
        "tasks": [
            {
                "id": "T1",
                "title": "demo task",
                "file_paths_touched": [],
                "tests_enabled": [],
                "agent_assignment": {"subagent": "coder", "model": "inherit"},
            }
        ],
    }


def test_schema_accepts_carried_phase_2():
    # The whole point of Part B: the carried "Phase 2" now validates.
    validate_against_schema(_minimal_orchestrator("Phase 2"), "orchestrator")


def test_schema_still_accepts_legacy_phase_3():
    # Additive widening — orchestrators emitted before the carry-forward still pass.
    validate_against_schema(_minimal_orchestrator("Phase 3"), "orchestrator")


def test_schema_still_rejects_garbage_phase():
    with pytest.raises(SchemaRejectedError):
        validate_against_schema(_minimal_orchestrator("Phase 9"), "orchestrator")
