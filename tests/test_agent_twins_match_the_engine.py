"""The shipped agent/command prose must not contradict the shipped engine.

These files are prompts: an agent reads them and acts. A stale instruction here
is not a documentation nit — it steers the subagent into the wrong file. Two
specific drifts are pinned:

1. **`tests_enabled` lives in the orchestrator, not `_state.json`.** The state
   writer has never emitted a `tests_enabled` field, but `agents/coder.md` and
   `agents/verifier.md` used to tell the coder and the verifier to read it from
   `_state.json`. A subagent following that instruction finds nothing and either
   refuses or, worse, proceeds with an empty gate.

2. **The prefixes `agents/planner.md` teaches must be the ones the validator
   recognises.** The planner authors `tests_enabled` entries; `_verify_plan`
   grades them. If the doc names a prefix the validator does not know (or
   vice-versa), plans are authored to a contract nothing enforces.

Also pinned: `commands/code.md`'s `test_gate` junction notice names a `bin/verify`
subcommand that actually exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bin._verify_plan.strict import (
    TYPED_GATE_COMMAND_PREFIX,
    VERIFICATION_KIND_MARKER_PREFIX,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AGENTS = _REPO_ROOT / "agents"
_COMMANDS = _REPO_ROOT / "commands"


def _read(path: Path) -> str:
    assert path.is_file(), f"shipped prompt missing: {path}"
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 1. the canonical tests_enabled source                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["coder.md", "verifier.md"])
def test_twin_names_the_orchestrator_as_canonical(name: str) -> None:
    body = _read(_AGENTS / name)
    assert "<slug>_orchestrator.json" in body
    assert "canonical" in body.lower()


def _normalized(text: str) -> str:
    """Collapse markdown line-wrapping so proximity checks are meaningful."""
    return " ".join(text.split())


@pytest.mark.parametrize("name", ["coder.md", "verifier.md"])
def test_twin_never_sends_the_agent_to_state_json_for_tests_enabled(name: str) -> None:
    """The state writer has never emitted a `tests_enabled` field.

    Checked on normalized text: these files hard-wrap, so a sentence pairing
    `tests_enabled` with `_state.json` routinely spans two lines. Wherever the
    two appear near each other, the sentence must be DENYING the association.
    """
    body = _normalized(_read(_AGENTS / name))
    denials = ("statuses-only", "statuses only", "carries no", "no tests_enabled")

    for match in re.finditer(r"_state\.json", body):
        window = body[max(0, match.start() - 160) : match.end() + 160].lower()
        if "tests_enabled" not in window:
            continue  # a mention of _state.json unrelated to tests_enabled
        assert any(d in window for d in denials), (
            f"{name}: prose associates tests_enabled with _state.json without "
            f"denying it: ...{window.strip()}..."
        )


def test_the_state_writer_really_has_no_tests_enabled_field() -> None:
    """The claim the prose makes, checked against the shipped writer."""
    src = _read(_REPO_ROOT / "bin" / "_update_orchestrator" / "state_writer.py")
    assert "tests_enabled" not in src


# --------------------------------------------------------------------------- #
# 2. the planner teaches the prefixes the validator enforces                    #
# --------------------------------------------------------------------------- #


def test_planner_doc_names_the_validators_typed_gate_prefix() -> None:
    body = _read(_AGENTS / "planner.md")
    assert TYPED_GATE_COMMAND_PREFIX in body, TYPED_GATE_COMMAND_PREFIX


def test_planner_doc_names_the_validators_verification_kind_prefix() -> None:
    body = _read(_AGENTS / "planner.md")
    assert VERIFICATION_KIND_MARKER_PREFIX in body, VERIFICATION_KIND_MARKER_PREFIX


def test_planner_doc_marks_the_typed_gate_prefix_as_reserved() -> None:
    """`run_typed_gate_command` is shipped but unwired.

    An authored `gate_cmd:` entry would satisfy junction resolvability without
    ever running, so the planner must be told not to author one.
    """
    body = _read(_AGENTS / "planner.md")
    assert "RESERVED, not active" in body
    assert "do NOT author it" in body


def test_planner_doc_teaches_the_phantom_selector_rule() -> None:
    """The rule `_check_tests_enabled_contract` actually enforces."""
    body = _read(_AGENTS / "planner.md")
    assert "phantom selector" in body
    assert "file_paths_touched" in body
    assert "test_plan[]" in body  # where prose belongs instead


def test_planner_doc_points_at_the_prompt_and_the_validator() -> None:
    """The soft (prompt) and deterministic (validator) twins of one contract."""
    body = _read(_AGENTS / "planner.md")
    assert "TESTS_ENABLED_CONTRACT" in body
    assert "bin/_planner/prompt_templates.py" in body
    assert "bin/_verify_plan/strict.py" in body


# --------------------------------------------------------------------------- #
# 3. code.md's junction notice names a real subcommand                          #
# --------------------------------------------------------------------------- #


def test_code_md_junction_notice_names_a_real_subcommand() -> None:
    body = _read(_COMMANDS / "code.md")
    assert "bin/verify junction" in body

    parser_src = _read(_REPO_ROOT / "bin" / "_retry_loop" / "main.py")
    assert '"junction",' in parser_src, "code.md advertises a subcommand main.py lacks"
    assert "--junction" in parser_src


def test_no_twin_points_at_a_plan_doc_this_repo_does_not_ship() -> None:
    """Upstream's twins cite decision records that live in ITS plan history.

    Those are not carried here, so a citation that survives the port is a
    dangling pointer an agent will try to read. Template paths containing a
    `<placeholder>` are instructions, not citations, and are exempt.
    """
    for path in sorted(_AGENTS.glob("*.md")) + sorted(_COMMANDS.glob("*.md")):
        for token in _read(path).replace("`", " ").split():
            if not token.startswith("docs/plans/_closed/"):
                continue
            if "<" in token:
                continue  # e.g. docs/plans/_closed/<slug>/ — a template
            cited = _REPO_ROOT / token.rstrip(".,)")
            assert cited.exists(), f"{path.name} cites missing {token}"
