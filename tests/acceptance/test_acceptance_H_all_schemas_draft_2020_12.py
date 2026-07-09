"""H.1 — All 14 `schemas/*.schema.json` parse + validate under Draft202012Validator.

Per Sonnet M-5: closes the 12-of-14-unvalidated gap.
"""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


def test_every_schema_parses_under_draft_2020_12(repo_root):
    """H.1: each schemas/*.schema.json validates as Draft 2020-12 metaschema."""
    import jsonschema
    from jsonschema import Draft202012Validator

    schemas_dir = repo_root / "schemas"
    schema_files = sorted(schemas_dir.glob("*.schema.json"))
    assert schema_files, "No schemas/*.schema.json files found"

    failures: list[tuple[str, str]] = []
    for path in schema_files:
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append((path.name, f"JSON parse: {exc}"))
            continue
        try:
            Draft202012Validator.check_schema(schema)
        except jsonschema.SchemaError as exc:
            failures.append((path.name, f"Draft 2020-12 meta-validation: {exc.message[:100]}"))

    assert not failures, (
        "Schema files failing Draft202012Validator meta-validation:\n"
        + "\n".join(f"  {n}: {e}" for n, e in failures)
    )
