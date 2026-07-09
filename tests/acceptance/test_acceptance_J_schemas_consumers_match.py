"""J.6 — Every `schemas/*.schema.json` has at least one consumer in `bin/`.

Per inventory:
- Source: Opus B-1 + quickstart Schemas block.
- Expected outcome: each schema file referenced (by name) in at least one
  `bin/*.py` module; no orphan schemas.
"""

from __future__ import annotations

import pytest
from pathlib import Path


pytestmark = pytest.mark.acceptance


# Schemas that are imported by sibling components outside bin/ — exempt from
# the bin/-side consumer requirement. Document each exemption with rationale.
EXEMPT_SCHEMAS = {
    "process_graph.schema.json",  # consumed by src/process_graph/, not bin/

    # Per Pass 5 Finding 2 (Option c hybrid resolution):
    # - `failure_v1.schema.json` IS now consumed at write time by
    #   `bin/_eval_common/failure_capture.py::_validate_failure_row`
    #   (added 2026-05-22). Highest-blast-radius §J writer gets runtime
    #   validation.
    # - The other 4 §J schemas below ship as spec-only / test-validated
    #   surfaces. The §J writers DO NOT validate at runtime; emission shape
    #   discipline is enforced via the test suite at
    #   `tests/test_eval_trace/test_*_schema.py`.
    #   This is a deliberate design choice per plan §J (operator-as-terminator
    #   keeps the eval surface lightweight); document any change here.
    "span_v1.schema.json",            # spec for _spans.jsonl; tests enforce
    "scores_emission_v1.schema.json", # spec for _scores.jsonl emissions
    "scores_label_v1.schema.json",    # spec for _scores.jsonl operator labels
    "regression_case_v1.schema.json", # spec for _regression_cases/<id>.json
}


def test_every_schema_has_a_consumer(repo_root):
    """J.6: every schema in schemas/ is referenced by at least one bin/ module."""
    schemas_dir = repo_root / "schemas"
    schemas = [p.name for p in schemas_dir.glob("*.schema.json")]
    assert schemas, "No schema files found in schemas/"

    bin_dir = repo_root / "bin"
    py_files = list(bin_dir.rglob("*.py"))
    all_source = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in py_files)

    orphans: list[str] = []
    for schema_name in schemas:
        if schema_name in EXEMPT_SCHEMAS:
            continue
        # Look for the basename appearing anywhere in bin/ source.
        if schema_name not in all_source:
            orphans.append(schema_name)

    assert not orphans, (
        "Schemas in schemas/ with no consumer in bin/*.py:\n"
        + "\n".join(f"  - {s}" for s in orphans)
        + "\n(Either remove the schema, wire it up, or add to EXEMPT_SCHEMAS with rationale.)"
    )
