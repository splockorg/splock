"""Constrained-rubric schemas for the runtime §F.9 reviewer.

Per splock plan §F.5 (test-step R1-R5) + §F.9.3 (phase-boundary
schemas) + splock implplan §F.impl.5 (encoding + forward-compat
discipline).

This module is the SOLE source of truth for the rubric shapes. Both
`iteration_loop.py` (test-step) and `phase_boundary_review.py` (the
runtime §F.9 gate orchestrator) import these schemas; the test suite
asserts shape invariants directly.

Three rubric schemas
--------------------

1. ``TEST_STEP_RUBRIC_SCHEMA_V1`` (§F.5 + §F.impl.5):
   R1 (root cause) / R2 (what missed) / R3 (next action) / R4 (tampering)
   / R5 (confidence). R4 is the load-bearing question — when
   ``R4 == "yes-flagged"`` the iteration loop halts regardless of test
   exit code (§F.impl.3 step 5d).

2. ``PLAN_TO_IMPLPLAN_RUBRIC_SCHEMA_V1`` (§F.9.3):
   R1 (recon coverage) / R2 (defensibility) / R3 (ambiguities) plus a
   ``terminal_shape`` closed enum (``READY`` / ``NEEDS_REVISION`` /
   ``HALT``). The terminal shape drives `phase_boundary_review.py`
   dispatch per §F.impl.8.

3. ``IMPLPLAN_TO_CODE_RUBRIC_SCHEMA_V1`` (§F.9.3):
   R1 (tests_enabled consistency) / R2 (concreteness) / R3 (DAG-ness) /
   R4 (sealed-paths) plus the same ``terminal_shape`` closed enum.

Terminal-shape contract — load-bearing per anchor §4a.3
-------------------------------------------------------

The phase-boundary reviewer's structured-output response has exactly
ONE top-level ``terminal_shape`` field whose value is one of three
closed enums:

- ``READY`` — boundary cleared; chain driver advances to next phase.
- ``NEEDS_REVISION`` — chain driver re-spawns the prior step agent
  with the reviewer's structured rubric answer appended as input.
- ``HALT`` — chain driver halts via exit code 10
  (`EXIT_PHASE_BOUNDARY_HALT`); task transitions to `deferred`;
  morning-review entry written via `halt_handoff.write_halt_entry`.

This is the RUNTIME §F.9 gate verdict shape — distinct from the
BUILD-TIME orchestrator §5 Sonnet review junctions (which produced the
substrate that ships this file). The build-time junctions emit
BLOCKER / MAJOR / MINOR / NIT findings against the substrate being
constructed; the runtime gates emit READY / NEEDS_REVISION / HALT
against the agent-emitted artifact being reviewed.

Forward-compat discipline
-------------------------

`is_supported_version(...)` mirrors the §B.impl schema-registry pattern.
Unknown future ``rubric_version`` values are refused identically by
every consumer; the runtime CLI exits with code 5
(`EXIT_UNSUPPORTED_SCHEMA_VERSION`).
"""

from __future__ import annotations

from typing import Any, Literal


# ----------------------------------------------------------------------
# Closed-enum constants
# ----------------------------------------------------------------------

# R4 (tampering check) — load-bearing per §F.5 lines 1788-1794
R4_TAMPERING_VALUES: frozenset[str] = frozenset({"no", "yes-flagged", "unclear"})

# R5 (confidence)
R5_CONFIDENCE_VALUES: frozenset[str] = frozenset({"high", "medium", "low"})

# Phase-boundary terminal verdict — drives §F.impl.8 dispatch
TERMINAL_SHAPE_VALUES: frozenset[str] = frozenset(
    {"READY", "NEEDS_REVISION", "HALT"}
)

# plan→implplan R1 / R2 / R3
PLAN_R1_VALUES: frozenset[str] = frozenset(
    {"complete", "partial", "gaps_identified"}
)
PLAN_R2_VALUES: frozenset[str] = frozenset({"defensible", "flag"})
PLAN_R3_VALUES: frozenset[str] = frozenset({"none_found", "flag"})

# implplan→code R1 / R2 / R3 / R4
IMPLPLAN_R1_VALUES: frozenset[str] = frozenset({"consistent", "mismatch"})
IMPLPLAN_R2_VALUES: frozenset[str] = frozenset({"concrete", "flag"})
IMPLPLAN_R3_VALUES: frozenset[str] = frozenset({"dag", "cycle_detected"})
IMPLPLAN_R4_VALUES: frozenset[str] = frozenset({"clean", "flag"})


# ----------------------------------------------------------------------
# JSON Schema constants (Draft 2020-12)
# ----------------------------------------------------------------------

TEST_STEP_RUBRIC_SCHEMA_V1: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "rubric_version",
        "iteration",
        "R1_root_cause",
        "R2_what_missed",
        "R3_next_action",
        "R4_tampering",
        "R5_confidence",
        "_metadata",
    ],
    "properties": {
        "rubric_version": {"type": "integer", "const": 1},
        "iteration": {"type": "integer", "minimum": 1},
        "R1_root_cause": {"type": "string", "minLength": 1},
        "R2_what_missed": {"type": "string", "minLength": 1},
        "R3_next_action": {"type": "string", "minLength": 1},
        "R4_tampering": {"enum": sorted(R4_TAMPERING_VALUES)},
        "R5_confidence": {"enum": sorted(R5_CONFIDENCE_VALUES)},
        "_metadata": {
            "type": "object",
            "additionalProperties": True,
            "required": [
                "test_files_edited_this_iteration",
                "test_runner_exit_code",
                "iteration_diff_lines_added",
                "iteration_diff_lines_removed",
            ],
            "properties": {
                "test_files_edited_this_iteration": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "test_runner_exit_code": {"type": "integer"},
                "iteration_diff_lines_added": {"type": "integer", "minimum": 0},
                "iteration_diff_lines_removed": {"type": "integer", "minimum": 0},
            },
        },
    },
}
"""Test-step rubric (§F.5 lines 1788-1817). Encoded verbatim from plan
spec; the schema constant is the source of truth for SDK structured-output
binding (Anthropic ``output_config={"format":{"type":"json_schema","schema":...}}``).

R4 is the load-bearing field — see `is_tampering_flagged(...)` for the
halt-trigger predicate."""


PLAN_TO_IMPLPLAN_RUBRIC_SCHEMA_V1: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "rubric_version",
        "boundary",
        "R1_recon_coverage",
        "R2_deferred_now_defensibility",
        "R3_structural_ambiguities",
        "terminal_shape",
    ],
    "properties": {
        "rubric_version": {"type": "integer", "const": 1},
        "boundary": {"const": "plan_to_implplan"},
        "R1_recon_coverage": {"enum": sorted(PLAN_R1_VALUES)},
        "R1_unaccounted_items": {"type": "array", "items": {"type": "string"}},
        "R2_deferred_now_defensibility": {"enum": sorted(PLAN_R2_VALUES)},
        "R2_suspect_entries": {"type": "array", "items": {"type": "string"}},
        "R3_structural_ambiguities": {"enum": sorted(PLAN_R3_VALUES)},
        "R3_ambiguity_list": {"type": "array", "items": {"type": "string"}},
        "terminal_shape": {"enum": sorted(TERMINAL_SHAPE_VALUES)},
        "reviewer_notes": {"type": "string"},
    },
}
"""plan → implplan boundary rubric (§F.9.3). Active when chain driver
transitions from `/plan` phase output to `/implplan` phase consumption."""


IMPLPLAN_TO_CODE_RUBRIC_SCHEMA_V1: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "rubric_version",
        "boundary",
        "R1_tests_enabled_consistency",
        "R2_concrete_placeholders",
        "R3_dependency_graph",
        "R4_sealed_paths",
        "terminal_shape",
    ],
    "properties": {
        "rubric_version": {"type": "integer", "const": 1},
        "boundary": {"const": "implplan_to_code"},
        "R1_tests_enabled_consistency": {"enum": sorted(IMPLPLAN_R1_VALUES)},
        "R1_mismatched_task_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "R2_concrete_placeholders": {"enum": sorted(IMPLPLAN_R2_VALUES)},
        "R2_placeholder_sites": {
            "type": "array",
            "items": {"type": "string"},
        },
        "R3_dependency_graph": {"enum": sorted(IMPLPLAN_R3_VALUES)},
        "R3_cycle_members": {"type": "array", "items": {"type": "string"}},
        "R4_sealed_paths": {"enum": sorted(IMPLPLAN_R4_VALUES)},
        "R4_suspect_references": {
            "type": "array",
            "items": {"type": "string"},
        },
        "terminal_shape": {"enum": sorted(TERMINAL_SHAPE_VALUES)},
        "reviewer_notes": {"type": "string"},
    },
}
"""implplan → code boundary rubric (§F.9.3). Active when chain driver
transitions from `/implplan` phase output to `/code` phase consumption."""


# ----------------------------------------------------------------------
# Schema lookup + forward-compat
# ----------------------------------------------------------------------

RubricKind = Literal["test_step", "plan_to_implplan", "implplan_to_code"]

_SCHEMA_REGISTRY: dict[str, dict[int, dict[str, Any]]] = {
    "test_step": {1: TEST_STEP_RUBRIC_SCHEMA_V1},
    "plan_to_implplan": {1: PLAN_TO_IMPLPLAN_RUBRIC_SCHEMA_V1},
    "implplan_to_code": {1: IMPLPLAN_TO_CODE_RUBRIC_SCHEMA_V1},
}


def resolve_schema(kind: RubricKind, version: int = 1) -> dict[str, Any]:
    """Resolve the rubric schema for ``(kind, version)``.

    Per §F.impl.5 forward-compat: unknown versions raise
    ``UnsupportedRubricVersionError``. The CLI maps this to exit code 5.
    """
    if kind not in _SCHEMA_REGISTRY:
        raise ValueError(
            f"unknown rubric kind: {kind!r} "
            f"(supported: {sorted(_SCHEMA_REGISTRY)})"
        )
    by_version = _SCHEMA_REGISTRY[kind]
    if version not in by_version:
        raise UnsupportedRubricVersionError(
            kind=kind,
            version=version,
            supported=sorted(by_version),
        )
    return by_version[version]


def is_supported_version(kind: RubricKind, version: int) -> bool:
    """Mirror §B.impl.6 schema-registry forward-compat predicate.

    Returns True iff ``(kind, version)`` is in `_SCHEMA_REGISTRY`.
    Consumed by `iteration_loop.run_iteration` step 5d (and by
    `phase_boundary_review.run_boundary_review`) before parsing a
    received rubric payload.
    """
    return kind in _SCHEMA_REGISTRY and version in _SCHEMA_REGISTRY[kind]


class UnsupportedRubricVersionError(ValueError):
    """Raised when a received rubric version is not in the registry.

    Per §F.impl.5 + §B.impl.6 forward-compat: refuse identically across
    consumers. Caller maps to exit code 5
    (`EXIT_UNSUPPORTED_SCHEMA_VERSION`).
    """

    def __init__(self, *, kind: str, version: int, supported: list[int]) -> None:
        super().__init__(
            f"rubric kind={kind!r} version={version} unsupported "
            f"(supported versions: {supported})"
        )
        self.kind = kind
        self.version = version
        self.supported = supported


# ----------------------------------------------------------------------
# Verdict predicates (single source of truth for halt-trigger semantics)
# ----------------------------------------------------------------------

def is_tampering_flagged(rubric: dict[str, Any]) -> bool:
    """Return True iff the test-step rubric's R4 is the halt-trigger value.

    Per §F.5 lines 1788-1794: R4 == "yes-flagged" means the iteration
    weakened test assertions (removed assertions / broadened acceptable
    inputs / added skips/xfails/sys.exits). The chain driver halts at
    the current iteration regardless of test exit code.

    "unclear" does NOT trigger halt — it surfaces a recommendation in
    R3 but lets the loop continue per the rubric semantics
    (`iteration_loop.run_iteration` step 5e).
    """
    return rubric.get("R4_tampering") == "yes-flagged"


def terminal_shape_of(rubric: dict[str, Any]) -> str:
    """Return the phase-boundary rubric's terminal verdict.

    Closed enum: ``READY`` / ``NEEDS_REVISION`` / ``HALT``. Raises
    ``ValueError`` if the value is missing or not in `TERMINAL_SHAPE_VALUES`
    — the chain driver (consumer in `phase_boundary_review`) treats this
    as a structurally-impossible Sonnet response.
    """
    shape = rubric.get("terminal_shape")
    if shape not in TERMINAL_SHAPE_VALUES:
        raise ValueError(
            f"terminal_shape={shape!r} not in closed enum "
            f"{sorted(TERMINAL_SHAPE_VALUES)}"
        )
    return shape


__all__ = [
    "IMPLPLAN_R1_VALUES",
    "IMPLPLAN_R2_VALUES",
    "IMPLPLAN_R3_VALUES",
    "IMPLPLAN_R4_VALUES",
    "IMPLPLAN_TO_CODE_RUBRIC_SCHEMA_V1",
    "PLAN_R1_VALUES",
    "PLAN_R2_VALUES",
    "PLAN_R3_VALUES",
    "PLAN_TO_IMPLPLAN_RUBRIC_SCHEMA_V1",
    "R4_TAMPERING_VALUES",
    "R5_CONFIDENCE_VALUES",
    "RubricKind",
    "TERMINAL_SHAPE_VALUES",
    "TEST_STEP_RUBRIC_SCHEMA_V1",
    "UnsupportedRubricVersionError",
    "is_supported_version",
    "is_tampering_flagged",
    "resolve_schema",
    "terminal_shape_of",
]
