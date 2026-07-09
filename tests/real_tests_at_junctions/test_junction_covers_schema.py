"""tests/real_tests_at_junctions/test_junction_covers_schema.py

Per `real_tests_at_junctions` T4 test_plan (SC6) — the junction -> task
binding contract (optional `covers[]` on the junction object +
`junction_covering_set` resolution helper):

  1. T4-covers-optional        — a junction WITHOUT covers[] still
                                 validates (backward-compat with today's
                                 emission shape {id, after_task, kind}).
  2. T4-covers-accepted        — a junction WITH covers[] (array of
                                 task ids) validates.
  3. T4-default-covering-set   — the loader returns the documented
                                 default (all prior tasks through
                                 after_task, tasks-array order) when
                                 covers[] is absent.
  4. T4-additional-properties-not-broken — a junction with a bogus key
                                 is still rejected; the additive change
                                 is limited to covers[].

Plus the loud-failure cases (bogus covers id, empty covers, unresolvable
after_task), the explicit-covers-wins-verbatim case, the TaskId-pattern
identity pin, and a backward-compatibility pin: an orchestrator whose
junctions carry NO covers[] still validates against the updated schema,
and `junction_covering_set` on its test_gate resolves to the array-order
prefix through after_task.

Schema validation rides `bin._render_plan.json_loader.validate_against_schema`.
Synthetic payloads throughout; deterministic by construction.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bin._orchestrator_query.orchestrator_loader import junction_covering_set
from bin._render_plan.json_loader import (
    SchemaRejectedError,
    validate_against_schema,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = _REPO_ROOT / "schemas" / "orchestrator_v1.schema.json"


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


def _task(tid: str) -> dict:
    return {
        "id": tid,
        "title": f"task {tid}",
        "file_paths_touched": [],
        "tests_enabled": [],
        "agent_assignment": {"subagent": "coder", "model": "inherit"},
    }


def _orch(task_ids: list[str], junctions: list[dict]) -> dict:
    """Schema-complete synthetic orchestrator payload."""
    return {
        "schema_version": 1,
        "slug": "synthetic",
        "phase": "Phase 3",
        "plan_ref": "synthetic_plan.json",
        "tasks": [_task(tid) for tid in task_ids],
        "junctions": junctions,
    }


def _junction(**overrides) -> dict:
    base = {"id": "J1", "after_task": "T3", "kind": "test_gate"}
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# 1. T4-covers-optional                                                        #
# --------------------------------------------------------------------------- #


def test_t4_covers_optional():
    """A junction WITHOUT covers[] still validates — today's emission
    shape {id, after_task, kind} is untouched (backward-compat)."""
    payload = _orch(["T1", "T2", "T3"], [_junction()])
    validate_against_schema(payload, "orchestrator", source_path="x")


# --------------------------------------------------------------------------- #
# 2. T4-covers-accepted                                                        #
# --------------------------------------------------------------------------- #


def test_t4_covers_accepted():
    """A junction WITH covers[] (array of TaskId-shaped strings)
    validates against the updated schema."""
    payload = _orch(
        ["T1", "T2", "T3"], [_junction(covers=["T2", "T3"])]
    )
    validate_against_schema(payload, "orchestrator", source_path="x")


def test_t4_covers_item_must_match_task_id_pattern():
    """covers[] items carry the TaskId pattern: a non-T-prefixed string
    is schema-rejected."""
    payload = _orch(
        ["T1", "T2", "T3"], [_junction(covers=["task_2"])]
    )
    with pytest.raises(SchemaRejectedError):
        validate_against_schema(payload, "orchestrator", source_path="x")


def test_t4_covers_empty_array_rejected():
    """An explicitly-empty covers[] is schema-rejected (minItems 1) — a
    vacuous test_gate must not be expressible; omit the field instead."""
    payload = _orch(["T1", "T2", "T3"], [_junction(covers=[])])
    with pytest.raises(SchemaRejectedError):
        validate_against_schema(payload, "orchestrator", source_path="x")


def test_t4_covers_pattern_identical_to_task_id_pattern():
    """The covers[] item pattern is the SAME definition the tasks' `id`
    uses — drift between the two surfaces would re-open the
    orchestrator-vs-log TaskId split."""
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    task_id = schema["properties"]["tasks"]["items"]["properties"]["id"]
    covers_items = schema["properties"]["junctions"]["items"]["properties"][
        "covers"
    ]["items"]
    assert covers_items["pattern"] == task_id["pattern"]


# --------------------------------------------------------------------------- #
# 3. T4-default-covering-set                                                   #
# --------------------------------------------------------------------------- #


def test_t4_default_covering_set():
    """covers[] absent → the documented default: ALL prior tasks through
    after_task, in tasks-array order (plan SC6: 'a documented default of
    all prior tasks through after_task')."""
    payload = _orch(["T1", "T2", "T3", "T4", "T5"], [_junction(after_task="T3")])
    assert junction_covering_set(payload, payload["junctions"][0]) == [
        "T1",
        "T2",
        "T3",
    ]


def test_t4_default_covering_set_first_task_boundary():
    """after_task == first task → covering set is exactly that task."""
    payload = _orch(["T1", "T2"], [_junction(after_task="T1")])
    assert junction_covering_set(payload, payload["junctions"][0]) == ["T1"]


def test_t4_explicit_covers_wins_verbatim():
    """Explicit covers[] wins verbatim — order preserved, no expansion
    to the array prefix, no dedupe-reordering."""
    payload = _orch(
        ["T1", "T2", "T3", "T4"], [_junction(covers=["T4", "T2"])]
    )
    assert junction_covering_set(payload, payload["junctions"][0]) == [
        "T4",
        "T2",
    ]


def test_t4_covers_bogus_task_id_raises_loudly():
    """A covers[] entry naming a non-existent task id raises ValueError
    naming the junction AND the bogus id."""
    payload = _orch(["T1", "T2"], [_junction(covers=["T1", "T99"])])
    with pytest.raises(ValueError) as ei:
        junction_covering_set(payload, payload["junctions"][0])
    msg = str(ei.value)
    assert "J1" in msg
    assert "T99" in msg


def test_t4_covers_explicit_empty_raises():
    """The loader mirrors the schema: an explicitly-empty covers[] (fed
    as a raw unvalidated dict) raises rather than yielding a vacuous
    always-pass covering set."""
    payload = _orch(["T1", "T2"], [_junction(covers=[])])
    with pytest.raises(ValueError, match="J1"):
        junction_covering_set(payload, payload["junctions"][0])


def test_t4_default_unresolvable_after_task_raises():
    """covers[] absent and after_task not a defined task id → loud
    ValueError (plan-time strict-junction-resolution rejects this
    upstream; the helper stays defensive for raw dicts)."""
    payload = _orch(["T1", "T2"], [_junction(after_task="T9")])
    with pytest.raises(ValueError) as ei:
        junction_covering_set(payload, payload["junctions"][0])
    msg = str(ei.value)
    assert "J1" in msg
    assert "T9" in msg


# --------------------------------------------------------------------------- #
# 4. T4-additional-properties-not-broken                                       #
# --------------------------------------------------------------------------- #


def test_t4_additional_properties_not_broken():
    """A junction with a bogus key is still rejected — the additive
    change is limited to covers[]; additionalProperties:false and the
    required triple are unchanged."""
    payload = _orch(["T1", "T2", "T3"], [_junction(bogus_key="x")])
    with pytest.raises(SchemaRejectedError):
        validate_against_schema(payload, "orchestrator", source_path="x")


def test_t4_required_triple_unchanged():
    """Dropping any of {id, after_task, kind} still rejects — covers[]
    did not relax the required set."""
    for missing in ("id", "after_task", "kind"):
        junction = _junction(covers=["T1"])
        del junction[missing]
        payload = _orch(["T1", "T2", "T3"], [junction])
        with pytest.raises(SchemaRejectedError):
            validate_against_schema(payload, "orchestrator", source_path="x")


# --------------------------------------------------------------------------- #
# backward-compatibility pin: the pre-covers[] emission shape                   #
#                                                                               #
# Upstream this reads the source repo's OWN closed plan artifact. That file is  #
# that repo's history rather than framework code, so the pin is reconstructed   #
# here against a synthetic orchestrator of the same shape: a multi-task plan    #
# whose junctions carry NO covers[], exactly as every orchestrator emitted      #
# before this schema change does.                                               #
# --------------------------------------------------------------------------- #


def _legacy_shape_orchestrator() -> dict:
    """Nine tasks; a test_gate after T5 and a phase_boundary after T9.

    Neither junction declares `covers[]` — the shipped emission shape prior to
    this additive schema change.
    """
    return _orch(
        [f"T{i}" for i in range(1, 10)],
        [
            _junction(id="J1", after_task="T5", kind="test_gate"),
            _junction(id="J2", after_task="T9", kind="phase_boundary"),
        ],
    )


def test_t4_pre_covers_orchestrator_still_validates():
    """A junctions-without-covers[] orchestrator still validates: the schema
    change is additive and backward-compatible with the shipped shape."""
    validate_against_schema(
        _legacy_shape_orchestrator(), "orchestrator", source_path="x"
    )


def test_t4_pre_covers_test_gate_resolves_to_array_order_prefix():
    """J1 (test_gate, after_task T5, no covers[]) resolves to [T1..T5] per the
    array-order-prefix rule — the exact set a junction-time oracle consolidates.

    The phase_boundary after T9 covers the whole plan by the same rule, which is
    what keeps the two junction kinds from silently sharing a covering set.
    """
    payload = _legacy_shape_orchestrator()
    (j1,) = [j for j in payload["junctions"] if j["kind"] == "test_gate"]
    assert j1["after_task"] == "T5"
    assert junction_covering_set(payload, j1) == ["T1", "T2", "T3", "T4", "T5"]

    (j2,) = [j for j in payload["junctions"] if j["kind"] == "phase_boundary"]
    assert junction_covering_set(payload, j2) == [f"T{i}" for i in range(1, 10)]
