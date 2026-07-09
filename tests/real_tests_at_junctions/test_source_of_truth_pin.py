"""tests/real_tests_at_junctions/test_source_of_truth_pin.py

Per `real_tests_at_junctions` T2 test_plan (SC5) — pins the tests_enabled
source-of-truth decision:

    canonical source = `<slug>_orchestrator.json` `tasks[].tests_enabled`;
    `_state.json` carries task statuses only (it has NO tests_enabled
    field anywhere).

Three pinned surfaces:

(a) T2-r1-prompt-names-fed-file — the implplan→code boundary reviewer
    prompt rendered by `build_briefing` names `_orchestrator.json` (the
    file `_read_orchestrator_shape` actually feeds it) and never claims
    `_state.json` carries anything.
(b) T2-canonical-source-constant — `CANONICAL_TESTS_ENABLED_SOURCE` +
    `resolve_tests_enabled` in `bin/_retry_loop/briefing.py` are the
    single resolver later tasks (T3 strict validator, T5 spawners)
    import; the path shape matches `bin/_retry_loop/main.py`'s own
    `plan_dir / f"{args.slug}_orchestrator.json"` derivation.
(c) T2-state-json-has-no-tests_enabled — regression pin that the shipped
    `_state.json` writer (`bin/_update_orchestrator/state_writer.py`)
    contains no `tests_enabled` write at all.

Sealed-file contract alignment (coder.md / verifier.md staged copies) is
doc-review per the T2 test_plan — deliberately NOT asserted here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bin._retry_loop.briefing import (
    CANONICAL_TESTS_ENABLED_SOURCE,
    build_briefing,
    canonical_tests_enabled_path,
    resolve_tests_enabled,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


def _write_orchestrator(plan_dir: Path, slug: str, tasks: list[dict]) -> Path:
    """Write a minimal synthetic orchestrator JSON into ``plan_dir``."""
    path = plan_dir / f"{slug}_orchestrator.json"
    path.write_text(
        json.dumps({"schema_version": 1, "slug": slug, "tasks": tasks}),
        encoding="utf-8",
    )
    return path


def _render_implplan_to_code_prompt(plan_dir: Path, slug: str = "sot_demo") -> str:
    """Render the implplan→code boundary prompt via the SOLE builder entry."""
    _write_orchestrator(
        plan_dir,
        slug,
        [
            {
                "id": "T1",
                "status": "ready",
                "depends_on": [],
                "tests_enabled": ["tests/sot_demo/test_t1.py"],
            }
        ],
    )
    return build_briefing(
        slug=slug,
        iteration_n=1,
        rubric_kind="implplan_to_code",
        plan_dir=plan_dir,
    )


# --------------------------------------------------------------------------- #
# (a) R1 reviewer prompt names the file actually fed to it                     #
# --------------------------------------------------------------------------- #


def test_r1_rubric_section_names_orchestrator_json(tmp_path):
    """The R1 tests_enabled-consistency rubric block points the reviewer at
    `_orchestrator.json` — the file `_read_orchestrator_shape` actually
    reads — not `_state.json`."""
    prompt = _render_implplan_to_code_prompt(tmp_path)
    assert "## R1 tests_enabled consistency" in prompt
    r1_block = prompt.split("## R1 tests_enabled consistency", 1)[1].split("## R2", 1)[0]
    assert "_orchestrator.json" in r1_block
    assert "_state.json" not in r1_block


def test_boundary_prompt_never_claims_state_json(tmp_path):
    """No section of the rendered implplan→code prompt claims any input
    comes from `_state.json` (R3's depends_on claim was the same
    wrong-file pattern and is pinned here too)."""
    prompt = _render_implplan_to_code_prompt(tmp_path)
    assert "_state.json" not in prompt
    # The prompt names the real fed substrate at least twice: the rubric's
    # R1 reference plus the user-side section header.
    assert prompt.count("_orchestrator.json") >= 2


# --------------------------------------------------------------------------- #
# (b) canonical-source constant + resolver                                     #
# --------------------------------------------------------------------------- #


def test_canonical_source_constant_is_orchestrator_json():
    """The constant formats to exactly `<slug>_orchestrator.json`."""
    assert CANONICAL_TESTS_ENABLED_SOURCE == "{slug}_orchestrator.json"
    assert (
        CANONICAL_TESTS_ENABLED_SOURCE.format(slug="demo")
        == "demo_orchestrator.json"
    )


def test_canonical_path_matches_main_py_derivation():
    """`bin/_retry_loop/main.py` derives the orchestrator path as
    `plan_dir / f"{args.slug}_orchestrator.json"`; the canonical helper
    must produce the identical shape — no split source."""
    main_src = (_REPO_ROOT / "bin" / "_retry_loop" / "main.py").read_text(
        encoding="utf-8"
    )
    assert '{args.slug}_orchestrator.json' in main_src
    derived = canonical_tests_enabled_path(Path("docs/plans/demo"), "demo")
    assert derived == Path("docs/plans/demo/demo_orchestrator.json")
    assert derived.name.endswith("_orchestrator.json")


def test_resolve_tests_enabled_returns_task_list(tmp_path):
    """The resolver returns the named task's tests_enabled from a
    synthetic orchestrator — and a declared-empty task yields []."""
    _write_orchestrator(
        tmp_path,
        "sot_demo",
        [
            {"id": "T1", "tests_enabled": ["tests/a/test_a.py"]},
            {
                "id": "T2",
                "tests_enabled": [
                    "tests/real_tests_at_junctions/test_source_of_truth_pin.py"
                ],
            },
            {"id": "T3", "tests_enabled": []},
        ],
    )
    assert resolve_tests_enabled(tmp_path, "sot_demo", "T2") == [
        "tests/real_tests_at_junctions/test_source_of_truth_pin.py"
    ]
    assert resolve_tests_enabled(tmp_path, "sot_demo", "T3") == []


def test_resolve_tests_enabled_fails_loud_on_missing_file(tmp_path):
    """A missing orchestrator file raises — never a silent empty set
    (a silent empty would let a test gate pass vacuously)."""
    with pytest.raises(FileNotFoundError):
        resolve_tests_enabled(tmp_path, "absent_slug", "T1")


def test_resolve_tests_enabled_fails_loud_on_unknown_task(tmp_path):
    """An unknown task id raises KeyError — same loud-failure contract."""
    _write_orchestrator(
        tmp_path, "sot_demo", [{"id": "T1", "tests_enabled": []}]
    )
    with pytest.raises(KeyError):
        resolve_tests_enabled(tmp_path, "sot_demo", "T99")


# --------------------------------------------------------------------------- #
# (c) _state.json has no tests_enabled field — regression pin                  #
# --------------------------------------------------------------------------- #


def test_state_writer_source_never_writes_tests_enabled():
    """The shipped `_state.json` writer contains no `tests_enabled`
    anywhere — statuses (+ retry/telemetry bookkeeping) only. This is the
    "shipped _state.json has no such field" finding, pinned so a future
    writer change that introduces a second carrier fails this test and
    forces a deliberate source-of-truth re-decision."""
    src = (
        _REPO_ROOT / "bin" / "_update_orchestrator" / "state_writer.py"
    ).read_text(encoding="utf-8")
    assert "tests_enabled" not in src
