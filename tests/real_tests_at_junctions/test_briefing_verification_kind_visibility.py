"""The boundary briefing must SURFACE verification_kind markers (SC3).

First-field-deployment defect (2026-07-19): the implplan→code boundary
reviewer's briefing exposed only `id`/`depends_on`/`tests_enabled` per
task in `orchestrator_shape.depends_on_graph` — NOT `test_plan` — so
the SC3 either/or contract's exemption marker (`tests_enabled: []` +
a `verification_kind:` test_plan entry) was invisible. Reviewers
flagged correctly-markered tasks as R1 mismatches every iteration; with
the unified cap, a marker-carrying DAG could deterministically exhaust
to exit 17. Two production slugs hit it the same day a third passed
with an identically-shaped task — reviewer-disposition variance on
missing data.

The fix has two halves, pinned here:

1. `_read_orchestrator_shape` rows carry `verification_kinds`
   (extracted per the canonical `verification_kind:` prefix contract
   from `bin/_verify_plan/strict.py`);
2. the R1 rubric section in the rendered boundary prompt states the
   exemption so the surfaced field is load-bearing, not decorative.

Run from the splock repo root with the project venv active.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._retry_loop.briefing import (  # noqa: E402
    _read_orchestrator_shape,
    _verification_kinds,
)

SLUG = "vk_slug"


def _write_orchestrator(plan_dir: Path, tasks: list[dict]) -> None:
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / f"{SLUG}_orchestrator.json").write_text(
        json.dumps({"schema_version": 1, "slug": SLUG, "tasks": tasks}),
        encoding="utf-8",
    )


def test_markered_task_surfaces_its_kinds(tmp_path):
    _write_orchestrator(tmp_path, [
        {"id": "T1", "depends_on": [], "tests_enabled": [],
         "test_plan": [
             {"test_id": "verification_kind: artifact_review",
              "asserts": "n/a", "fixture": "n/a"},
         ]},
        {"id": "T2", "depends_on": ["T1"],
         "tests_enabled": ["tests/test_x.py::test_y"],
         "test_plan": [
             {"test_id": "tests/test_x.py::test_y",
              "asserts": "real", "fixture": "real"},
         ]},
    ])
    shape = _read_orchestrator_shape(tmp_path, SLUG)
    rows = {r["id"]: r for r in shape["depends_on_graph"]}
    # the markered task's exemption is now VISIBLE to the reviewer...
    assert rows["T1"]["verification_kinds"] == ["artifact_review"]
    assert rows["T1"]["tests_enabled"] == []
    # ...and an ordinary pytest-graded task shows none
    assert rows["T2"]["verification_kinds"] == []


def test_extraction_mirrors_the_canonical_prefix_contract():
    task = {"test_plan": [
        {"test_id": "verification_kind: manual_smoke"},
        {"test_id": "verification_kind:operator_signoff"},  # no space: valid
        {"test_id": "tests/test_a.py::test_b"},             # plain entry
        {"test_id": "verification_kinds: typo"},            # near-miss: NOT a marker
        "not-a-dict",                                        # tolerated
    ]}
    assert _verification_kinds(task) == ["manual_smoke", "operator_signoff"]
    assert _verification_kinds({}) == []
    assert _verification_kinds({"test_plan": None}) == []


def test_r1_rubric_section_states_the_exemption(tmp_path, monkeypatch):
    """The surfaced field must be load-bearing: the rendered boundary
    prompt's R1 section tells the reviewer markered tasks are consistent."""
    source = (REPO_ROOT / "bin" / "_retry_loop" / "briefing.py").read_text(
        encoding="utf-8")
    r1_idx = source.find("## R1 tests_enabled consistency")
    assert r1_idx != -1
    r1_section = source[r1_idx:source.find("## R2", r1_idx)]
    assert "verification_kinds" in r1_section
    assert "Do not flag markered tasks" in r1_section