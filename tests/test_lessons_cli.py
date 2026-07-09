"""`bin/lessons` — the per-plan lessons.md CLI, and the three roots it touches.

This engine was a shipped-surface orphan: `schemas/lessons_v1.schema.json`,
`.claude/templates/lessons_entry.md.template`, and two `KNOWN_WRITERS` entries
(`bin/lessons`, `bin/lessons:add`) all shipped, with no engine behind them. Worse,
`bin/_planner/main.py::_read_lessons` shells out to `bin/lessons query --json`
so that a lenient parse drops malformed H2 blocks *before they reach the planner
LLM* — with the CLI absent, every `/plan` run silently fell back to a raw file
read and fed those blocks straight through.

Three distinct roots are in play, and upstream resolves all of them off the same
`parents[2]` walk. Under an installed plugin that is the plugin cache:

- `docs/plans/<slug>/lessons.md` is **adopter data** -> `plans_dir()`
- `schemas/lessons_v1.schema.json` is a **read-only plugin asset** -> `schemas_dir()`
- `.claude/templates/lessons_entry.md.template` is a **plugin asset with an
  adopter override** -> project-first, plugin-second, per file

Conflating them writes the adopter's lessons into the plugin tree.
"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path
from typing import Any

import pytest

from bin._env_paths import plans_dir, plugin_root, schemas_dir
from bin._lessons import query as query_mod
from bin._lessons import validate as validate_mod
from bin._lessons import writer as writer_mod
from bin._lessons.parser import (
    LessonEntry,
    LessonsEntryMalformedError,
    parse_lessons_md,
)
from bin._lessons.query import query_lessons
from bin._lessons.validate import SchemaValidationError, validate_schema
from bin._lessons.writer import _template_path, append_lesson, resolve_plan_dir

_VALID_SOURCE = "commit:5ef5db2"


def _entry(task: str = "T1", title: str = "a lesson") -> LessonEntry:
    return LessonEntry(
        date="2026-07-09",
        title=title,
        task=task,
        approach="what was attempted",
        failure_mode="how it failed",
        rejection="why it was rejected",
        reattempt="what to do instead",
        source=_VALID_SOURCE,
    )


# --------------------------------------------------------------------------- #
# the three roots                                                              #
# --------------------------------------------------------------------------- #


def test_lessons_data_resolves_to_the_adopter_plans_dir() -> None:
    """lessons.md is adopter data, not a plugin asset."""
    assert writer_mod._PLANS_DIR == plans_dir()
    assert query_mod._PLANS_DIR == plans_dir()


def test_schema_resolves_to_the_plugin_and_actually_exists() -> None:
    """`schemas/` is the one root that legitimately stays anchored to the plugin."""
    assert validate_mod._SCHEMA_PATH == schemas_dir() / "lessons_v1.schema.json"
    assert validate_mod._SCHEMA_PATH.is_file()


def test_template_falls_back_to_the_plugin_when_the_adopter_has_none(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    resolved = _template_path()
    assert resolved == plugin_root() / ".claude" / "templates" / "lessons_entry.md.template"
    assert resolved.is_file()


def test_template_prefers_an_adopter_override(tmp_path, monkeypatch) -> None:
    """Per-file override: an adopter may replace this one template only."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    override = tmp_path / ".claude" / "templates" / "lessons_entry.md.template"
    override.parent.mkdir(parents=True)
    override.write_text("## custom\n", encoding="utf-8")

    assert _template_path() == override


def test_resolve_plan_dir_honours_an_explicit_base(tmp_path: Path) -> None:
    assert resolve_plan_dir("demo", base=tmp_path) == tmp_path / "demo"


# --------------------------------------------------------------------------- #
# add -> query round trip, entirely inside a tmp adopter                        #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def adopter(tmp_path: Path) -> Path:
    (tmp_path / "demo").mkdir()
    return tmp_path


def test_append_then_query_round_trip(adopter: Path) -> None:
    append_lesson("demo", _entry(title="first"), plans_dir=adopter)
    append_lesson("demo", _entry(task="T2", title="second"), plans_dir=adopter)

    entries = query_lessons("demo", plans_dir=adopter)
    assert [e.title for e in entries] == ["first", "second"]
    assert (adopter / "demo" / "lessons.md").is_file()


def test_query_can_filter_by_task(adopter: Path) -> None:
    append_lesson("demo", _entry(task="T1", title="one"), plans_dir=adopter)
    append_lesson("demo", _entry(task="T2", title="two"), plans_dir=adopter)

    entries = query_lessons("demo", task="T2", plans_dir=adopter)
    assert [e.title for e in entries] == ["two"]


def test_query_of_an_absent_lessons_file_is_empty_not_an_error(adopter: Path) -> None:
    assert query_lessons("demo", plans_dir=adopter) == []


# --------------------------------------------------------------------------- #
# the lenient parse the planner depends on                                      #
# --------------------------------------------------------------------------- #


_MALFORMED = """\
## 2026-07-09 — a good one

**Task:** T1

**Approach attempted:** a

**Failure mode:** b

**Why this approach was rejected:** c

**Re-attempt criteria:** d

**Source:** commit:5ef5db2

## 2026-07-09 — a broken one

this block is missing every required field
"""


def test_lenient_parse_drops_a_malformed_block() -> None:
    """This is the whole reason `_read_lessons` shells out to the CLI.

    A malformed H2 block must never reach the planner LLM.
    """
    entries = parse_lessons_md(_MALFORMED, lenient=True)
    assert [e.title for e in entries] == ["a good one"]


def test_strict_parse_raises_on_the_same_input() -> None:
    with pytest.raises(LessonsEntryMalformedError):
        parse_lessons_md(_MALFORMED, lenient=False)


def test_query_is_lenient_by_default_and_strict_on_request(adopter: Path) -> None:
    (adopter / "demo" / "lessons.md").write_text(_MALFORMED, encoding="utf-8")

    assert len(query_lessons("demo", plans_dir=adopter)) == 1
    with pytest.raises(LessonsEntryMalformedError):
        query_lessons("demo", plans_dir=adopter, strict=True)


# --------------------------------------------------------------------------- #
# schema validation                                                             #
# --------------------------------------------------------------------------- #


def test_a_freeform_source_is_schema_rejected() -> None:
    """`source` must be a traceable ref, not prose — the shipped schema says so.

    This is what makes a lesson auditable: every entry points at a commit, an
    orchestrator-log line, or a morning-review row.
    """
    bad = dataclasses.replace(_entry(), source="PR #24")
    with pytest.raises(SchemaValidationError):
        validate_schema(bad)


def test_a_traceable_source_passes_the_schema() -> None:
    validate_schema(_entry())  # must not raise


# --------------------------------------------------------------------------- #
# the planner's lookup of the wrapper                                           #
# --------------------------------------------------------------------------- #


def test_planner_looks_for_the_wrapper_under_the_plugin_not_the_adopter(
    tmp_path, monkeypatch
) -> None:
    """`bin/lessons` ships with the plugin; `lessons.md` lives in the adopter.

    Upstream derived the wrapper from `plan_dir.parent.parent.parent` — the
    adopter root — so under an installed plugin it was never found and the
    lenient parse was silently skipped.
    """
    import bin._planner.main as planner_main

    plan_dir = tmp_path / "docs" / "plans" / "demo"
    plan_dir.mkdir(parents=True)

    seen: dict[str, Any] = {}

    def _fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    planner_main._read_lessons(plan_dir)

    assert seen["argv"][0] == str(plugin_root() / "bin" / "lessons")
    # `query` takes the slug POSITIONALLY; only `list` accepts `--slug`. Passing
    # `--slug` here made argparse exit 1, so `_read_lessons` returned "" even
    # when the CLI existed. An earlier version of this test enshrined that bug.
    assert seen["argv"][1:4] == ["query", "demo", "--json"]
    # ...and the adopter root is NOT where it looked for the wrapper.
    assert str(tmp_path) not in seen["argv"][0]


def test_planner_tells_the_subprocess_which_adopter_to_read(tmp_path, monkeypatch) -> None:
    """The wrapper resolves its plans dir from the environment, not from argv.

    Without this the subprocess would query whatever repo the planner happened to
    be invoked from — typically the plugin — and return nothing for a slug that
    plainly exists in `plan_dir`.
    """
    import bin._planner.main as planner_main

    plan_dir = tmp_path / "docs" / "plans" / "demo"
    plan_dir.mkdir(parents=True)

    seen: dict[str, Any] = {}

    def _fake_run(argv, **kwargs):
        seen["env"] = kwargs.get("env") or {}
        return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    planner_main._read_lessons(plan_dir)

    assert seen["env"]["CLAUDE_PROJECT_DIR"] == str(tmp_path.resolve())


def test_the_wrapper_the_planner_reaches_for_actually_ships() -> None:
    wrapper = plugin_root() / "bin" / "lessons"
    assert wrapper.is_file(), "the planner shells out to a wrapper that must exist"
