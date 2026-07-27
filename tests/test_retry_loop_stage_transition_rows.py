"""`bin/verify test-step` emits STAGE-level rows, not task rows.

The test-step path has no task id in scope — it is invoked as
`test-step <slug> --chain-id <id>` and its pre-flight reads a `tests_enabled`
UNION across all tasks. So `task_id` is null BY CONTRACT, and null is
schema-legal (`TaskId` = `^T[A-Za-z0-9_-]+$` | null).

The defect these tests pin against is not the null: it is that the row
previously carried no discriminator, so a log consumer reconciling transitions
against tasks saw a bare `wip -> done` with no task and could not tell a stage
row from an unattributed task transition. `event_type` is the discriminator,
matching the in-file convention (`pause_inject_consumed`, `boundary_counter_reset`).
"""

from __future__ import annotations

import json

from bin._jsonl_log.schema import validate_row
from bin._retry_loop.halt_handoff import _emit_deferral_row
from bin._retry_loop.iteration_loop import (
    EVENT_TYPE_TEST_STEP_HALT,
    EVENT_TYPE_TEST_STEP_STAGE,
    IterationContext,
    _emit_iteration_transition,
)


def _rows(plan_dir):
    lines = (plan_dir / "_orchestrator_log.jsonl").read_text().splitlines()
    return [json.loads(ln) for ln in lines if ln.strip()]


def _emit_iteration(tmp_path, transition_to="done"):
    ctx = IterationContext(
        plan_dir=tmp_path,
        slug="demo",
        chain_id="c1",
        iteration_n=0,
        max_retries=3,
    )
    _emit_iteration_transition(
        ctx,
        transition_to=transition_to,
        reason="entry verify PASSED (verify_first short-circuit)",
    )
    return _rows(tmp_path)


def test_stage_row_is_discriminated_not_an_unattributed_task_done(tmp_path):
    """A reconciler must be able to tell this apart from a task transition."""
    (row,) = _emit_iteration(tmp_path)
    assert row["task_id"] is None, "never fabricate a task id for a stage row"
    assert row["event_type"] == EVENT_TYPE_TEST_STEP_STAGE
    assert row["transition"] == {"from": "wip", "to": "done"}
    assert row["emitted_by"] == "bin/verify"


def test_stage_row_stays_schema_valid_with_the_discriminator(tmp_path):
    """event_type is payload-level and free-form; it must not break validation."""
    (row,) = _emit_iteration(tmp_path)
    validate_row(row)  # raises SchemaValidationError on regression


def test_halt_deferral_row_is_discriminated_too(tmp_path):
    """The halt path had the identical defect; fixing only half re-creates it."""
    _emit_deferral_row(
        plan_dir=tmp_path,
        slug="demo",
        chain_id="c1",
        halt_reason="max_retries_exhausted",
        iteration_count=3,
    )
    (row,) = _rows(tmp_path)
    assert row["task_id"] is None
    assert row["event_type"] == EVENT_TYPE_TEST_STEP_HALT
    assert row["transition"] == {"from": "wip", "to": "deferred"}
    validate_row(row)


def test_every_null_task_row_this_module_emits_carries_a_discriminator(tmp_path):
    """The invariant, stated once: null task_id => event_type present.

    This is what makes the rows classifiable. If a future emitter on this path
    adds a null-task row without a discriminator, this reddens.
    """
    _emit_iteration(tmp_path, transition_to="done")
    _emit_iteration(tmp_path, transition_to="wip")
    rows = _rows(tmp_path)
    assert len(rows) == 2
    for row in rows:
        if row.get("task_id") is None:
            assert row.get("event_type"), f"null-task row lacks event_type: {row}"
