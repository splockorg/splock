"""`bin/render_spans` — derive OpenInference-shape spans from a plan's logs.

The strongest orphan case in this backport. Before this port, FOUR independent
registries in this repo referenced a CLI that did not exist:

- `bin/_jsonl_log/writers.py` allowlisted `bin/render_spans` as a log emitter;
- `bin/_cli_lint/exemptions.py` granted it a lint exemption;
- `bin/_eval_gate/touch_paths.py` declared `bin/render_spans` and
  `bin/_render_spans/**` as touch paths;
- `hooks/sealed_paths.txt` SEALED `docs/plans/*/_spans.jsonl` — the output file
  of a tool that was never shipped.

And `schemas/span_v1.schema.json` was referenced by nothing at all. The last two
tests below bind those registries to the engine, so the next orphan of this shape
fails a test instead of sitting unnoticed.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from bin._env_paths import plans_dir, plugin_root
from bin._render_spans import main as main_mod
from bin._render_spans.derive import derive
from bin._render_spans.exit_codes import EXIT_OK, EXIT_USAGE
from bin._render_spans.span_shape import SPAN_ROOT_PARENT

_LOG_ROWS = [
    {
        "ts": "2026-07-09T10:00:00Z",
        "chain_id": "c1",
        "session_id": "s1",
        "event_type": "transition",
        "emitted_by": "chain_driver",
        "task_id": "T1",
        "transition": "wip",
        "reason": "",
    },
    {
        "ts": "2026-07-09T10:05:00Z",
        "chain_id": "c1",
        "session_id": "s1",
        "event_type": "transition",
        "emitted_by": "chain_driver",
        "task_id": "T1",
        "transition": "done",
        "reason": "tests green",
    },
]

_CHAIN_SESSIONS = {
    "chains": {
        "c1": {
            "phases": [
                {"started_at": "2026-07-09T10:00:00Z", "ended_at": "2026-07-09T10:05:00Z"}
            ]
        }
    }
}


def _plan_dir(root: Path, *, chains: bool = True, rows=_LOG_ROWS) -> Path:
    plan_dir = root / "docs" / "plans" / "demo"
    plan_dir.mkdir(parents=True)
    (plan_dir / "_orchestrator_log.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    if chains:
        (plan_dir / "_chain_sessions.json").write_text(
            json.dumps(_CHAIN_SESSIONS), encoding="utf-8"
        )
    return plan_dir


@pytest.fixture()
def span_schema() -> dict:
    return json.loads(
        (plugin_root() / "schemas" / "span_v1.schema.json").read_text(encoding="utf-8")
    )


# --------------------------------------------------------------------------- #
# the adopter's logs are the input, the adopter's plan dir the output           #
# --------------------------------------------------------------------------- #


def test_plans_dir_resolves_to_the_adopter(tmp_path, monkeypatch) -> None:
    """Upstream walked `parents[2]/docs/plans` — the plugin cache."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert main_mod._plans_dir() == plans_dir() == tmp_path.resolve() / "docs" / "plans"


def test_writes_spans_into_the_adopter_plan_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    plan_dir = _plan_dir(tmp_path)

    assert main_mod.main(["demo"]) == EXIT_OK
    assert (plan_dir / "_spans.jsonl").is_file()


def test_stdout_mode_writes_no_file(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    plan_dir = _plan_dir(tmp_path)

    assert main_mod.main(["demo", "--stdout"]) == EXIT_OK
    assert not (plan_dir / "_spans.jsonl").exists()
    assert capsys.readouterr().out.strip()


def test_missing_plan_dir_is_a_usage_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    assert main_mod.main(["__absent__"]) == EXIT_USAGE


# --------------------------------------------------------------------------- #
# the derived spans conform to the schema this repo already shipped             #
# --------------------------------------------------------------------------- #


def test_every_derived_span_validates_against_span_v1(tmp_path, span_schema) -> None:
    """`schemas/span_v1.schema.json` shipped with nothing to produce it."""
    spans = derive(_plan_dir(tmp_path))
    assert spans, "the fixture log should derive at least one span"
    for span in spans:
        jsonschema.validate(span.to_dict(), span_schema)


def test_a_chain_manifest_produces_a_chain_root_span(tmp_path) -> None:
    spans = derive(_plan_dir(tmp_path, chains=True))
    roots = [s for s in spans if s.parent_span_id == SPAN_ROOT_PARENT]
    assert roots, "a chain manifest must yield a root span"
    assert any(s.name == "chain:c1" for s in spans)


def test_rows_without_a_chain_manifest_are_marked_non_chain(tmp_path) -> None:
    """A log with no `_chain_sessions.json` still derives — flagged, not dropped."""
    spans = derive(_plan_dir(tmp_path, chains=False))
    assert spans
    assert any(s.attributes.get("non_chain") for s in spans)


def test_span_ids_are_deterministic(tmp_path) -> None:
    """Same input, same ids — spans are content-addressed, so re-derivation is idempotent."""
    first = derive(_plan_dir(tmp_path / "a"))
    second = derive(_plan_dir(tmp_path / "b"))
    assert [s.span_id for s in first] == [s.span_id for s in second]
    assert all(s.span_id.startswith("span_") for s in first)


def test_a_corrupt_log_row_does_not_abort_derivation(tmp_path, span_schema) -> None:
    plan_dir = tmp_path / "docs" / "plans" / "demo"
    plan_dir.mkdir(parents=True)
    (plan_dir / "_orchestrator_log.jsonl").write_text(
        json.dumps(_LOG_ROWS[0]) + "\n" + "{ this is not json\n" + json.dumps(_LOG_ROWS[1]) + "\n",
        encoding="utf-8",
    )
    spans = derive(plan_dir)
    assert spans, "a single corrupt row must not lose the whole trace"
    for span in spans:
        jsonschema.validate(span.to_dict(), span_schema)


# --------------------------------------------------------------------------- #
# the registries that referenced this CLI now have an engine behind them        #
# --------------------------------------------------------------------------- #


def test_the_log_emitter_allowlist_names_a_cli_that_exists() -> None:
    from bin._jsonl_log.writers import KNOWN_WRITERS

    assert "bin/render_spans" in KNOWN_WRITERS
    assert (plugin_root() / "bin" / "render_spans").is_file()


def test_the_cli_lint_exemption_names_a_cli_that_exists() -> None:
    from bin._cli_lint.exemptions import EXEMPTIONS

    assert "bin/render_spans" in EXEMPTIONS
    assert (plugin_root() / "bin" / "_render_spans" / "main.py").is_file()


def test_the_sealed_output_path_is_one_this_cli_actually_writes() -> None:
    """`hooks/sealed_paths.txt` sealed `_spans.jsonl` before anything wrote it."""
    sealed = (plugin_root() / "hooks" / "sealed_paths.txt").read_text(encoding="utf-8")
    assert "docs/plans/*/_spans.jsonl" in sealed
    assert '"_spans.jsonl"' in (
        plugin_root() / "bin" / "_render_spans" / "main.py"
    ).read_text(encoding="utf-8")
