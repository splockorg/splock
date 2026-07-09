"""`bin/chain-status` and `bin/state-divergence-check` — read-only chain observability.

Both were dangling registry entries: `bin/_cli_lint/exemptions.py` granted a lint
exemption to each, for CLIs this repo did not ship. The last test in this file
turns that class of orphan into a failing test rather than a silent lie — every
lint-exemption key must name a file the linter can actually lint.

Both tools read the ADOPTER's plan dirs. Upstream anchors them on `parents[2]`
(resp. `parent.parent.parent`), which under an installed plugin is the plugin
cache — so `chain-status` would report on the plugin's own empty plan tree, and
`state-divergence-check` would find no plans and report "no divergence" vacuously.
A vacuous all-clear from an auditing tool is worse than no tool.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bin._chain_status import main as chain_status_main
from bin._env_paths import plans_dir, plugin_root
from bin._state_divergence import main as divergence_main
from bin._state_divergence.replay import LogCorruptError, check_one, replay


def _log_row(task: str, to: str, ts: str = "2026-07-09T10:00:00Z") -> dict:
    return {
        "ts": ts,
        "event_type": "transition",
        "task_id": task,
        "transition": {"from": "wip", "to": to},
        "emitted_by": "chain_driver",
    }


def _plan_dir(root: Path, rows: list, state: dict | None = None) -> Path:
    plan_dir = root / "docs" / "plans" / "demo"
    plan_dir.mkdir(parents=True)
    (plan_dir / "_orchestrator_log.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    if state is not None:
        (plan_dir / "_state.json").write_text(json.dumps(state), encoding="utf-8")
    return plan_dir


# --------------------------------------------------------------------------- #
# both tools read the adopter's plans, not the plugin's                         #
# --------------------------------------------------------------------------- #


def test_chain_status_reads_the_adopter_plans_dir() -> None:
    assert chain_status_main._PLANS_DIR == plans_dir()


def test_state_divergence_reads_the_adopter_plans_dir() -> None:
    assert divergence_main.DOCS_PLANS == plans_dir()


# --------------------------------------------------------------------------- #
# divergence: clean / diverged / corrupt                                        #
# --------------------------------------------------------------------------- #


def test_state_agreeing_with_the_log_is_clean(tmp_path: Path) -> None:
    plan_dir = _plan_dir(
        tmp_path,
        [_log_row("T1", "done"), _log_row("T2", "wip")],
        {"tasks": {"T1": {"status": "done"}, "T2": {"status": "wip"}}},
    )
    report = check_one("demo", plan_dir)
    assert report["result"] == "clean"
    assert report["divergences"] == []


def test_state_disagreeing_with_the_log_is_reported_with_a_line_ref(tmp_path: Path) -> None:
    """The log is the authority; `_state.json` is a projection of it."""
    plan_dir = _plan_dir(
        tmp_path,
        [_log_row("T1", "done")],
        {"tasks": {"T1": {"status": "wip"}}},
    )
    report = check_one("demo", plan_dir)
    assert report["result"] == "diverged"
    (div,) = report["divergences"]
    assert div["task_id"] == "T1"
    assert div["log_says"] == "done"
    assert div["state_says"] == "wip"
    assert div["last_log_row_ref"] == "_orchestrator_log.jsonl:line=1"


def test_a_state_file_missing_a_task_the_log_knows_is_a_divergence(tmp_path: Path) -> None:
    plan_dir = _plan_dir(tmp_path, [_log_row("T1", "done")], {"tasks": {}})
    report = check_one("demo", plan_dir)
    assert report["result"] == "diverged"
    assert report["divergences"][0]["state_says"] is None


def test_a_non_object_transition_is_corruption_not_an_attribute_error(tmp_path: Path) -> None:
    """The shipped emitter writes `{"from": ..., "to": ...}`.

    A scalar means the row was not written by it. This tool exists to AUDIT
    logs, so a structurally wrong row must be reported as corruption — upstream
    crashed with `AttributeError: 'str' object has no attribute 'get'`.
    """
    plan_dir = _plan_dir(
        tmp_path,
        [{"ts": "2026-07-09T10:00:00Z", "task_id": "T1", "transition": "done"}],
        {"tasks": {"T1": {"status": "done"}}},
    )
    with pytest.raises(LogCorruptError, match="non-object `transition`"):
        replay(plan_dir / "_orchestrator_log.jsonl")

    # ...and the CLI surface degrades to a verdict rather than a traceback.
    assert check_one("demo", plan_dir)["result"] == "log_corrupt"


def test_rows_without_a_task_id_do_not_mutate_derived_state(tmp_path: Path) -> None:
    plan_dir = _plan_dir(
        tmp_path,
        [{"ts": "2026-07-09T10:00:00Z", "event_type": "chain_start"}, _log_row("T1", "done")],
        {"tasks": {"T1": {"status": "done"}}},
    )
    assert check_one("demo", plan_dir)["result"] == "clean"


# --------------------------------------------------------------------------- #
# chain-status                                                                  #
# --------------------------------------------------------------------------- #


def test_chain_status_reports_a_slug_with_no_live_chain(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(chain_status_main, "_PLANS_DIR", tmp_path / "docs" / "plans")
    _plan_dir(tmp_path, [_log_row("T1", "done")], {"tasks": {"T1": {"status": "done"}}})

    rc = chain_status_main.main(["--slug", "demo", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["chain_id"] is None  # no running sentinel


# --------------------------------------------------------------------------- #
# the registry that named these CLIs before they existed                        #
# --------------------------------------------------------------------------- #


def test_every_cli_lint_exemption_names_a_file_the_linter_can_lint() -> None:
    """`bin/_cli_lint/exemptions.py` keys are CLI paths, not labels.

    Before this port, two of them — `bin/chain-status` and
    `bin/state-divergence-check` — pointed at nothing. This guard is the
    generalized form of the orphan checks in `test_render_spans.py`.
    """
    from bin._cli_lint.exemptions import EXEMPTIONS

    dangling = [
        name
        for name in sorted(EXEMPTIONS)
        if name.startswith("bin/") and not (plugin_root() / name.split()[0]).exists()
    ]
    assert not dangling, f"cli-lint exempts CLIs that do not exist: {dangling}"


def test_no_wrapper_can_evade_the_hygiene_glob_with_brace_syntax() -> None:
    """`test_wrapper_project_resolution` selects wrappers containing the LITERAL
    `cd "$REPO_ROOT"`. Four wrappers wrote `cd "${REPO_ROOT}"` instead and were
    therefore never checked — so they silently missed the caller-pwd export that
    every other cd-to-root wrapper got. Ban the brace form so the selector cannot
    be evaded by punctuation.
    """
    import re

    offenders = [
        p.name
        for p in sorted((plugin_root() / "bin").iterdir())
        if p.is_file() and re.search(r'cd "\$\{REPO_ROOT\}"', p.read_text(errors="ignore"))
    ]
    assert not offenders, (
        f"these wrappers cd to the repo root using ${{REPO_ROOT}}, which hides them "
        f"from the wrapper-hygiene tests: {offenders}"
    )


def test_known_writers_entries_are_emitter_labels_not_paths() -> None:
    """Documents why the same guard cannot be applied to KNOWN_WRITERS.

    That allowlist names EMITTERS (`chain_driver_auto`, `session_start_auto`),
    some of which are not files at all, and `bin/security-dispatch` is the label
    for `bin/security-dispatch.sh`. Pinning that here so a future reader does not
    "fix" the asymmetry by deleting live entries.
    """
    from bin._jsonl_log.writers import KNOWN_WRITERS

    assert "chain_driver_auto" in KNOWN_WRITERS
    assert not (plugin_root() / "chain_driver_auto").exists()
    assert (plugin_root() / "bin" / "security-dispatch.sh").is_file()
