"""J.11 — Recorded SDK fixtures parse with current Anthropic SDK shape.

Per inventory:
- Source: Opus M-5 + Risk 1 (SDK API surface drift).
- Expected outcome: every recorded SDK response fixture parses as valid
  JSON or markdown; the schema-bound fixtures validate against their
  current schemas (catches silent SDK-shape drift between fixture
  recording date and now).
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path


pytestmark = pytest.mark.acceptance


FIXTURE_DIRS = [
    "tests/test_subagents/fixtures/recorded_responses",
    "tests/test_retry_loop/fixtures/recorded_sonnet",
]


def _collect_fixtures(repo_root: Path) -> list[Path]:
    out: list[Path] = []
    for rel in FIXTURE_DIRS:
        d = repo_root / rel
        if d.is_dir():
            out.extend(d.glob("*.json"))
            out.extend(d.glob("*.md"))
    return out


def test_every_recorded_fixture_parseable(repo_root):
    """J.11a: every recorded SDK fixture parses without error."""
    fixtures = _collect_fixtures(repo_root)
    assert fixtures, f"No recorded fixtures found in any of: {FIXTURE_DIRS}"

    failures: list[tuple[str, str]] = []
    for path in fixtures:
        rel = path.relative_to(repo_root)
        try:
            if path.suffix == ".json":
                json.loads(path.read_text(encoding="utf-8"))
            elif path.suffix == ".md":
                text = path.read_text(encoding="utf-8")
                if not text.strip():
                    failures.append((str(rel), "empty markdown fixture"))
        except json.JSONDecodeError as exc:
            failures.append((str(rel), f"JSON decode error: {exc}"))
        except UnicodeDecodeError as exc:
            failures.append((str(rel), f"Unicode decode error: {exc}"))

    assert not failures, (
        "Recorded fixture parse failures:\n"
        + "\n".join(f"  {p}: {e}" for p, e in failures)
    )


def test_emission_fixtures_validate_against_plan_schema(repo_root):
    """J.11b: call2_emission_*.json fixtures validate against plan_v1.schema.json.

    If the SDK schema bind (Anthropic structured outputs) drifts, this
    catches the drift between the recorded fixture shape and the schema
    we currently target.
    """
    import jsonschema

    schema_path = repo_root / "schemas" / "plan_v1.schema.json"
    if not schema_path.exists():
        pytest.skip("plan_v1.schema.json missing")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    fixtures_dir = repo_root / "tests/test_subagents/fixtures/recorded_responses"
    plan_emissions = list(fixtures_dir.glob("call2_emission_plan_*.json"))
    if not plan_emissions:
        pytest.skip("No call2_emission_plan_*.json fixtures to validate")

    validator = jsonschema.Draft202012Validator(schema)
    failures: list[tuple[str, list[str]]] = []
    for path in plan_emissions:
        payload = json.loads(path.read_text(encoding="utf-8"))
        errors = sorted(validator.iter_errors(payload), key=lambda e: e.path)
        if errors:
            failures.append((
                str(path.relative_to(repo_root)),
                [f"{list(e.path)}: {e.message}" for e in errors[:3]],
            ))

    assert not failures, (
        "Recorded plan-emission fixtures fail current schema validation "
        "(potential SDK-shape drift):\n"
        + "\n".join(f"  {p}:\n    " + "\n    ".join(errs) for p, errs in failures)
    )
