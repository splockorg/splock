"""tests/real_tests_at_junctions/test_collect_only_oracle.py

Per `real_tests_at_junctions` T5 test_plan (SC4) — the code/junction-time
resolvability oracle (`pytest --collect-only` exit-5 as collectability
truth, qa C.9) plus the junction-time covering-set hook:

  1. T5-exit5-not-collectable      — a selector that --collect-only
                                     reports as exit-5 classifies
                                     not-collectable, DISTINCT from a
                                     failing-but-collected selector
                                     (exit-1 at run time / exit-0 at
                                     collect time).
  2. T5-parametrized-node-id-native — a parametrized node-id with
                                     whitespace inside [...] collects via
                                     the oracle (not false-rejected by
                                     the cheap shape regex).
  3. T5-oracle-is-pure-upgrade     — the cheap is_runnable_pytest_selector
                                     pre-flight still runs FIRST; the
                                     oracle is layered after it (shape
                                     check, then collectability).
  4. T5-typed-command-recognized   — a `gate_cmd:` entry (T3's strict.py
                                     constant — imported, not
                                     re-declared) runs with exit-0=pass,
                                     NOT piped through pytest; the oracle
                                     classifies it as its own kind with
                                     no collect probe.
  5. T5-junction-hook-uses-covering-set — junction_collect_check
                                     consolidates T4's covering set and
                                     refuses advance when it does not
                                     collect / when the union is empty;
                                     entries outside the covering set
                                     neither satisfy nor block the gate.
                                     Includes the dogfood pin: this
                                     slug's REAL orchestrator J1 is
                                     advance-ok (via the CLI surface,
                                     exit 0).

All synthetic modules are tmp_path-local and tiny; every probe runs with
`-p no:cacheprovider` (pinned inside `collect_only_probe`) so subprocess
tests stay fast and side-effect-free. The code.md junction-halt notice
itself is sealed/doc-reviewed — behavior is validated here via the
oracle entrypoint, per the T5 test_plan fixture note.

Contract docs:
- docs/plans/_closed/real_tests_at_junctions/junction_covers_contract.md (SC6)
- bin/_verify_plan/strict.py::TYPED_GATE_COMMAND_PREFIX (SC3 prefix)
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from bin._retry_loop import exit_codes
from bin._retry_loop import main as main_mod
from bin._retry_loop import sdk_spawners
from bin._retry_loop.sdk_spawners import (
    ADVANCE_OK_CLASSIFICATIONS,
    COLLECT_COLLECTABLE,
    COLLECT_ERROR,
    COLLECT_NOT_COLLECTABLE,
    COLLECT_NOT_SELECTOR,
    COLLECT_TYPED_COMMAND,
    classify_tests_enabled_entry,
    collect_only_probe,
    is_runnable_pytest_selector,
    junction_collect_check,
    run_typed_gate_command,
)
from bin._verify_plan.strict import TYPED_GATE_COMMAND_PREFIX

_SLUG = "real_tests_at_junctions"
_J1 = "J1_test_gate_validator_oracle_core_T1_T5"


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def _failing_module(root: Path, name: str = "test_failing.py") -> Path:
    return _write(
        root / name,
        """\
        def test_always_fails():
            assert False, "collected fine; fails at run time"
        """,
    )


def _passing_module(root: Path, name: str = "test_passing.py") -> Path:
    return _write(
        root / name,
        """\
        def test_always_passes():
            assert True
        """,
    )


def _no_tests_module(root: Path, name: str = "helper_mod.py") -> Path:
    """A real on-disk .py file pytest collects ZERO tests from (exit 5)."""
    return _write(root / name, "X = 1\n")


def _orchestrator_payload(
    tasks: list[dict], junctions: list[dict], slug: str
) -> dict:
    return {
        "schema_version": 1,
        "slug": slug,
        "plan_ref": f"{slug}_plan.json",
        "tasks": tasks,
        "junctions": junctions,
    }


def _task(tid: str, tests_enabled: list[str]) -> dict:
    return {
        "id": tid,
        "title": f"task {tid}",
        "depends_on": [],
        "file_paths_touched": [],
        "tests_enabled": tests_enabled,
        "agent_assignment": {"subagent": "coder", "model": "inherit"},
    }


def _plant_plan_dir(
    plans_root: Path, slug: str, tasks: list[dict], junctions: list[dict]
) -> Path:
    plan_dir = plans_root / slug
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / f"{slug}_orchestrator.json").write_text(
        json.dumps(_orchestrator_payload(tasks, junctions, slug), indent=2),
        encoding="utf-8",
    )
    return plan_dir


# --------------------------------------------------------------------------- #
# 1. T5-exit5-not-collectable                                                  #
# --------------------------------------------------------------------------- #


class TestExit5NotCollectable:
    """Exit-5 (nothing collected) is DISTINCT from collected-but-failing."""

    def test_no_tests_module_classifies_not_collectable(self, tmp_path):
        mod = _no_tests_module(tmp_path)
        assert (
            collect_only_probe(mod.name, cwd=tmp_path)
            == COLLECT_NOT_COLLECTABLE
        ), (
            "a module pytest collects zero tests from (--collect-only exit "
            "5) must classify not_collectable"
        )

    def test_failing_module_classifies_collectable(self, tmp_path):
        """A failing-but-collected selector is COLLECTABLE — the oracle
        grades resolvability (collect-time exit 0), not greenness
        (run-time exit 1)."""
        mod = _failing_module(tmp_path)
        assert (
            collect_only_probe(mod.name, cwd=tmp_path) == COLLECT_COLLECTABLE
        )
        # The same selector FAILS at run time (exit 1) — pinning that the
        # two probes measure different things.
        run = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             mod.name],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            check=False,
        )
        assert run.returncode == 1, (
            f"sanity: the collected module must fail at run time (exit 1), "
            f"got {run.returncode}: {run.stdout}"
        )

    def test_phantom_node_id_in_existing_file_not_collectable(self, tmp_path):
        """A node-id pytest reports 'ERROR: not found' for (exit 4) is the
        unrecognized-selector case — also not_collectable. This is the
        phantom the cheap is_file() heuristic CANNOT catch (the file
        exists; the node inside it does not)."""
        mod = _failing_module(tmp_path)
        phantom = f"{mod.name}::test_does_not_exist"
        # The cheap shape check waves it through (file exists, no
        # whitespace in path component) ...
        assert is_runnable_pytest_selector(phantom, tmp_path) is True
        # ... and the oracle catches it.
        assert (
            collect_only_probe(phantom, cwd=tmp_path)
            == COLLECT_NOT_COLLECTABLE
        )

    def test_import_broken_module_is_collect_error_not_not_collectable(
        self, tmp_path
    ):
        """Import errors (collect exit 2) classify collect_error — a REAL
        failure surface, distinct from a selector that resolves to
        nothing."""
        mod = _write(
            tmp_path / "test_import_broken.py",
            """\
            import module_that_does_not_exist_xyz

            def test_unreachable():
                pass
            """,
        )
        assert collect_only_probe(mod.name, cwd=tmp_path) == COLLECT_ERROR


# --------------------------------------------------------------------------- #
# 2. T5-parametrized-node-id-native                                            #
# --------------------------------------------------------------------------- #


class TestParametrizedNodeIdNative:
    """Whitespace inside [...] is valid in pytest node-ids; the oracle
    handles it natively (selector passed as ONE argv element, no shell)."""

    SELECTOR = "test_param.py::test_p[has space id]"

    @pytest.fixture()
    def param_module(self, tmp_path):
        _write(
            tmp_path / "test_param.py",
            """\
            import pytest

            @pytest.mark.parametrize("x", [1], ids=["has space id"])
            def test_p(x):
                assert x == 1
            """,
        )
        return tmp_path

    def test_shape_check_does_not_false_reject(self, param_module):
        """The cheap pre-flight checks whitespace on the PATH component
        only — the parametrized id's internal space is not misclassified
        (plan SC4: B.3 resolved, the heuristic is NOT buggy)."""
        assert is_runnable_pytest_selector(self.SELECTOR, param_module) is True

    def test_oracle_collects_parametrized_node_id(self, param_module):
        assert (
            collect_only_probe(self.SELECTOR, cwd=param_module)
            == COLLECT_COLLECTABLE
        )


# --------------------------------------------------------------------------- #
# 3. T5-oracle-is-pure-upgrade                                                 #
# --------------------------------------------------------------------------- #


class TestOracleIsPureUpgrade:
    """The cheap pre-flight is retained and runs FIRST; the oracle is
    layered after it. Shape-invalid entries never reach the probe."""

    def test_shape_invalid_entries_never_reach_probe(self, tmp_path, monkeypatch):
        probe_calls: list[str] = []

        def _bomb_probe(selector, cwd=None, timeout_s=0):
            probe_calls.append(selector)
            raise AssertionError(
                f"collect_only_probe reached for shape-invalid entry "
                f"{selector!r} — the cheap pre-flight must run first"
            )

        monkeypatch.setattr(sdk_spawners, "collect_only_probe", _bomb_probe)

        for prose in (
            "CLI-version doc",
            "claude plugin validate . clean",
            "tests/not_on_disk_anywhere_xyz.py",
        ):
            assert (
                classify_tests_enabled_entry(prose, repo_root=tmp_path)
                == COLLECT_NOT_SELECTOR
            )
        assert probe_calls == []

    def test_shape_valid_entries_do_reach_probe(self, tmp_path, monkeypatch):
        """Layering order pin: shape check passes → the probe IS consulted
        (the oracle is an upgrade on top, not a replacement)."""
        mod = _passing_module(tmp_path)
        probe_calls: list[str] = []

        def _recording_probe(selector, cwd=None, timeout_s=0):
            probe_calls.append(selector)
            return COLLECT_COLLECTABLE

        monkeypatch.setattr(sdk_spawners, "collect_only_probe", _recording_probe)
        assert (
            classify_tests_enabled_entry(mod.name, repo_root=tmp_path)
            == COLLECT_COLLECTABLE
        )
        assert probe_calls == [mod.name]

    def test_preflight_refusal_fires_before_probe_in_main(
        self, tmp_path, monkeypatch, capsys
    ):
        """main._run_test_step: an all-prose union still refuses with the
        original no_runnable_tests envelope and the probe is NEVER
        called — the pre-flight is first in the layering."""
        plans_root = tmp_path / "docs" / "plans"
        slug = "synthetic-oracle-slug"
        _plant_plan_dir(
            plans_root,
            slug,
            tasks=[_task("T1", ["multi-sentence design prose, not a test"])],
            junctions=[],
        )
        monkeypatch.setattr(main_mod, "_PLANS_DIR", plans_root)

        def _bomb_probe(selector, cwd=None, timeout_s=0):
            raise AssertionError(
                "probe must not run when the cheap pre-flight already "
                "refused (no runnable selectors)"
            )

        monkeypatch.setattr(sdk_spawners, "collect_only_probe", _bomb_probe)

        import argparse

        rc = main_mod._run_test_step(
            argparse.Namespace(
                subcommand="test-step",
                slug=slug,
                chain_id="manual_t5_oracle",
                max_retries=None,
            )
        )
        assert rc == exit_codes.EXIT_USAGE
        err = capsys.readouterr().err
        assert "no_runnable_tests" in err

    def test_shape_valid_but_not_collectable_refuses_loudly_in_main(
        self, tmp_path, monkeypatch, capsys
    ):
        """main._run_test_step: a selector that passes shape but fails
        collection produces the loud not_collectable_tests refusal
        (extends the no_runnable_tests vocabulary, same envelope shape)
        BEFORE any SDK smoke check / coder spawn."""
        # Synthetic repo root carrying an on-disk module with no tests.
        repo = tmp_path / "repo"
        _no_tests_module(repo, "tests_lab/helper_mod.py")
        monkeypatch.setattr(sdk_spawners, "_repo_root", lambda: repo)

        plans_root = tmp_path / "docs" / "plans"
        slug = "synthetic-oracle-slug2"
        _plant_plan_dir(
            plans_root,
            slug,
            tasks=[_task("T1", ["tests_lab/helper_mod.py"])],
            junctions=[],
        )
        monkeypatch.setattr(main_mod, "_PLANS_DIR", plans_root)

        # If the refusal didn't fire, the next pre-loop step is the SDK
        # smoke check — bomb it to prove the oracle refused first.
        def _bomb_smoke():
            raise AssertionError(
                "smoke_check_sdk_available reached — the not-collectable "
                "refusal must fire before any SDK surface"
            )

        monkeypatch.setattr(
            sdk_spawners, "smoke_check_sdk_available", _bomb_smoke
        )

        import argparse

        rc = main_mod._run_test_step(
            argparse.Namespace(
                subcommand="test-step",
                slug=slug,
                chain_id="manual_t5_oracle",
                max_retries=None,
            )
        )
        assert rc == exit_codes.EXIT_USAGE
        err = capsys.readouterr().err
        assert "not_collectable_tests" in err, (
            f"expected the named not_collectable_tests refusal envelope; "
            f"got: {err}"
        )
        assert "tests_lab/helper_mod.py" in err

    def test_cheap_preflight_helper_still_exists_and_is_consulted(self):
        """Pin: the upgrade did not remove the cheap helper — both the
        shape check and the oracle are public module surface."""
        assert callable(sdk_spawners.is_runnable_pytest_selector)
        assert callable(sdk_spawners.partition_runnable_selectors)
        assert callable(sdk_spawners.collect_only_probe)


# --------------------------------------------------------------------------- #
# 4. T5-typed-command-recognized                                               #
# --------------------------------------------------------------------------- #


class TestTypedCommandRecognized:
    """gate_cmd: entries bypass pytest entirely; exit-0 = pass."""

    def test_prefix_is_imported_from_strict_not_redeclared(self):
        """T3's constant is the single source — sdk_spawners must carry
        the identical object, not a re-declared copy."""
        assert sdk_spawners.TYPED_GATE_COMMAND_PREFIX is TYPED_GATE_COMMAND_PREFIX

    def test_classifier_recognizes_typed_command_without_probe(
        self, monkeypatch
    ):
        def _bomb_probe(selector, cwd=None, timeout_s=0):
            raise AssertionError(
                "typed gate commands must NOT be collect-probed — they "
                "are not pytest selectors"
            )

        monkeypatch.setattr(sdk_spawners, "collect_only_probe", _bomb_probe)
        entry = f"{TYPED_GATE_COMMAND_PREFIX} true"
        assert classify_tests_enabled_entry(entry) == COLLECT_TYPED_COMMAND
        assert COLLECT_TYPED_COMMAND in ADVANCE_OK_CLASSIFICATIONS

    def test_typed_command_exit0_is_pass(self):
        result = run_typed_gate_command(f"{TYPED_GATE_COMMAND_PREFIX} true")
        assert result.returncode == 0

    def test_typed_command_nonzero_is_failure(self):
        result = run_typed_gate_command(f"{TYPED_GATE_COMMAND_PREFIX} false")
        assert result.returncode != 0

    def test_typed_command_runs_remainder_not_pytest(self, tmp_path):
        """The remainder of the entry is the command — run via subprocess
        from the given cwd, visibly NOT piped through pytest."""
        marker = "typed_cmd_marker.txt"
        result = run_typed_gate_command(
            f"{TYPED_GATE_COMMAND_PREFIX} touch {marker}", cwd=tmp_path
        )
        assert result.returncode == 0
        assert (tmp_path / marker).exists(), (
            "the typed command's side effect must land — the remainder is "
            "executed verbatim, not wrapped in a pytest invocation"
        )
        assert "pytest" not in " ".join(result.args)

    def test_non_typed_entry_raises(self):
        with pytest.raises(ValueError):
            run_typed_gate_command("tests/foo.py::test_bar")

    def test_empty_typed_command_raises(self):
        with pytest.raises(ValueError):
            run_typed_gate_command(f"{TYPED_GATE_COMMAND_PREFIX}   ")


# --------------------------------------------------------------------------- #
# 5. T5-junction-hook-uses-covering-set                                        #
# --------------------------------------------------------------------------- #


class TestJunctionHookUsesCoveringSet:
    """junction_collect_check consolidates T4's covering set and refuses
    advance when it does not collect."""

    def _repo_with_modules(self, tmp_path) -> Path:
        repo = tmp_path / "repo"
        _passing_module(repo, "tests_lab/test_ok.py")
        _no_tests_module(repo, "tests_lab/helper_mod.py")
        return repo

    def test_refuses_when_covered_selector_does_not_collect(self, tmp_path):
        repo = self._repo_with_modules(tmp_path)
        plans_root = tmp_path / "docs" / "plans"
        slug = "synthetic-junction-slug"
        plan_dir = _plant_plan_dir(
            plans_root,
            slug,
            tasks=[
                _task("T1", ["tests_lab/test_ok.py"]),
                _task("T2", ["tests_lab/helper_mod.py"]),  # collects nothing
            ],
            junctions=[
                {"id": "J1", "after_task": "T2", "kind": "test_gate"},
            ],
        )
        verdict = junction_collect_check(
            plan_dir, slug=slug, junction_id="J1", repo_root=repo
        )
        assert verdict["advance_ok"] is False
        assert verdict["refusal_reason"] == "not_collectable_entries"
        assert verdict["covering_set"] == ["T1", "T2"]
        by_entry = {e["entry"]: e["classification"] for e in verdict["entries"]}
        assert by_entry["tests_lab/test_ok.py"] == COLLECT_COLLECTABLE
        assert by_entry["tests_lab/helper_mod.py"] == COLLECT_NOT_COLLECTABLE

    def test_advances_when_all_covered_entries_collect_or_recognized(
        self, tmp_path
    ):
        repo = self._repo_with_modules(tmp_path)
        plans_root = tmp_path / "docs" / "plans"
        slug = "synthetic-junction-slug"
        plan_dir = _plant_plan_dir(
            plans_root,
            slug,
            tasks=[
                _task("T1", ["tests_lab/test_ok.py"]),
                _task("T2", [f"{TYPED_GATE_COMMAND_PREFIX} true"]),
            ],
            junctions=[
                {"id": "J1", "after_task": "T2", "kind": "test_gate"},
            ],
        )
        verdict = junction_collect_check(
            plan_dir, slug=slug, junction_id="J1", repo_root=repo
        )
        assert verdict["advance_ok"] is True
        assert verdict["refusal_reason"] is None
        kinds = {e["classification"] for e in verdict["entries"]}
        assert kinds == {COLLECT_COLLECTABLE, COLLECT_TYPED_COMMAND}

    def test_entries_outside_covering_set_do_not_affect_gate(self, tmp_path):
        """A selector belonging to a task OUTSIDE the gate's covering set
        must not satisfy OR block the gate (plan SC6, qa C.3): T3's
        garbage entry is invisible to a junction covering only T1."""
        repo = self._repo_with_modules(tmp_path)
        plans_root = tmp_path / "docs" / "plans"
        slug = "synthetic-junction-slug"
        plan_dir = _plant_plan_dir(
            plans_root,
            slug,
            tasks=[
                _task("T1", ["tests_lab/test_ok.py"]),
                _task("T2", []),
                _task("T3", ["tests_lab/helper_mod.py"]),  # outside covers
            ],
            junctions=[
                {
                    "id": "J1",
                    "after_task": "T2",
                    "kind": "test_gate",
                    "covers": ["T1"],
                },
            ],
        )
        verdict = junction_collect_check(
            plan_dir, slug=slug, junction_id="J1", repo_root=repo
        )
        assert verdict["covering_set"] == ["T1"]
        assert verdict["advance_ok"] is True
        assert [e["entry"] for e in verdict["entries"]] == [
            "tests_lab/test_ok.py"
        ]

    def test_empty_union_refuses_advance(self, tmp_path):
        """Empty consolidated union = NOT advance-ok — the
        silent-partial-coverage failure mode this slug exists to kill."""
        repo = self._repo_with_modules(tmp_path)
        plans_root = tmp_path / "docs" / "plans"
        slug = "synthetic-junction-slug"
        plan_dir = _plant_plan_dir(
            plans_root,
            slug,
            tasks=[_task("T1", []), _task("T2", [])],
            junctions=[
                {"id": "J1", "after_task": "T2", "kind": "test_gate"},
            ],
        )
        verdict = junction_collect_check(
            plan_dir, slug=slug, junction_id="J1", repo_root=repo
        )
        assert verdict["advance_ok"] is False
        assert verdict["refusal_reason"] == "empty_union"
        assert verdict["entries"] == []

    def test_unknown_junction_id_raises(self, tmp_path):
        repo = self._repo_with_modules(tmp_path)
        plans_root = tmp_path / "docs" / "plans"
        slug = "synthetic-junction-slug"
        plan_dir = _plant_plan_dir(
            plans_root,
            slug,
            tasks=[_task("T1", [])],
            junctions=[
                {"id": "J1", "after_task": "T1", "kind": "test_gate"},
            ],
        )
        with pytest.raises(ValueError, match="J9"):
            junction_collect_check(
                plan_dir, slug=slug, junction_id="J9", repo_root=repo
            )

    def test_cli_refuse_exits_phase_boundary_halt(
        self, tmp_path, monkeypatch, capsys
    ):
        """`bin/verify junction <slug> --junction <J-id>` on a refusing
        covering set exits 10 (EXIT_PHASE_BOUNDARY_HALT) with the JSON
        verdict on stdout."""
        repo = self._repo_with_modules(tmp_path)
        monkeypatch.setattr(sdk_spawners, "_repo_root", lambda: repo)
        plans_root = tmp_path / "docs" / "plans"
        slug = "synthetic-junction-slug"
        _plant_plan_dir(
            plans_root,
            slug,
            tasks=[_task("T1", ["tests_lab/helper_mod.py"])],
            junctions=[
                {"id": "J1", "after_task": "T1", "kind": "test_gate"},
            ],
        )
        monkeypatch.setattr(main_mod, "_PLANS_DIR", plans_root)

        rc = main_mod.main(["junction", slug, "--junction", "J1"])
        assert rc == exit_codes.EXIT_PHASE_BOUNDARY_HALT
        payload = json.loads(capsys.readouterr().out)
        assert payload["advance_ok"] is False
        assert payload["refusal_reason"] == "not_collectable_entries"

    def test_cli_unknown_junction_exits_usage(self, tmp_path, monkeypatch):
        repo = self._repo_with_modules(tmp_path)
        monkeypatch.setattr(sdk_spawners, "_repo_root", lambda: repo)
        plans_root = tmp_path / "docs" / "plans"
        slug = "synthetic-junction-slug"
        _plant_plan_dir(
            plans_root,
            slug,
            tasks=[_task("T1", [])],
            junctions=[
                {"id": "J1", "after_task": "T1", "kind": "test_gate"},
            ],
        )
        monkeypatch.setattr(main_mod, "_PLANS_DIR", plans_root)
        rc = main_mod.main(["junction", slug, "--junction", "J9"])
        assert rc == exit_codes.EXIT_USAGE

    # The upstream suite ends with a dogfood pin that drives this slug's own
    # closed orchestrator through the CLI. That artifact is the source repo's
    # history, not framework code, and it is not carried here. The junction
    # hook's real behaviour is pinned by the synthetic covering-set tests
    # above; the CLI seam is pinned once `bin/verify junction` lands.
