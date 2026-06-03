"""T-A schema-registry resolution smoke test.

Asserts ``bin._render_plan.schema_registry.resolve_schema`` resolves every kind
its ``SchemaKind`` enum supports against the shipped ``schemas/`` directory —
proving the "ship ``schemas/`` + ``_roster.json`` + ``bin/_*`` as a unit so
``parents[2]`` resolution is preserved" contract (SC-A).

Note on scope: ``resolve_schema``'s ``SchemaKind`` is a closed enum of FOUR kinds
(plan, orchestrator, state, plan_patch). The other shipped schemas
(span/failure/lessons/marker/scores_*/regression_case/orchestrator_log/
baseline_manifest/develop_plan_telemetry/env_inventory) ship as files and are
consumed by other loaders, but ``resolve_schema`` itself does not key them — so
"each kept kind" for THIS API == the four enum members. A separate assertion
confirms the dropped ``process_graph`` schema is absent and the kept
telemetry/env-inventory schema files are present.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._render_plan.schema_registry import resolve_schema, schemas_dir  # noqa: E402

RESOLVE_SCHEMA_KINDS = ("plan", "orchestrator", "state", "plan_patch")


def test_schemas_dir_points_at_shipped_schemas() -> None:
    assert schemas_dir() == REPO_ROOT / "schemas"
    assert schemas_dir().is_dir()


@pytest.mark.parametrize("kind", RESOLVE_SCHEMA_KINDS)
def test_resolve_schema_each_kept_kind(kind: str) -> None:
    schema = resolve_schema(kind, 1)
    assert isinstance(schema, dict) and schema, f"resolve_schema({kind!r}, 1) returned empty"
    # A JSON Schema doc carries at least a type/$schema/properties shape.
    assert any(k in schema for k in ("type", "$schema", "properties", "$defs")), (
        f"resolved {kind!r} does not look like a JSON Schema document"
    )


def test_kept_schema_files_present_and_process_graph_dropped() -> None:
    sdir = schemas_dir()
    # process_graph is DROPPED per SC-A.
    assert not (sdir / "process_graph.schema.json").exists(), (
        "process_graph.schema.json must NOT ship (SC-A DROP)"
    )
    # telemetry + env_inventory are explicitly KEPT.
    assert (sdir / "develop_plan_telemetry_v1.schema.json").exists()
    assert (sdir / "env_inventory_v1.schema.json").exists()
