"""tests/real_tests_at_junctions/test_contract_prompt_nudge.py

Per `real_tests_at_junctions` T1 test_plan #1 (SC1):

    T1-prompt-nudge-selector-language — asserts the Call-1/Call-2 prompt
    text in `bin/_planner/prompt_templates.py` contains the
    selector/typed-command nudge language and the test_plan[]-relocation
    instruction.

SC1 is the soft, authoring-side layer of the tests_enabled contract:
the planner's emission path must nudge toward runnable pytest selectors
(or typed gate commands) and relocate narrative testing intent into
`test_plan[]`. These tests pin stable phrases authored in
`TESTS_ENABLED_CONTRACT` and verify the contract actually renders into
BOTH the Call-1 reasoning prompt and the Call-2 emission system prompt —
not just that the constant exists.

Pure template-level tests: no SDK, no filesystem, no monkeypatch — pin
substrings on the module constants and on `render_call1_user` output.
(The deterministic enforcement twin is SC2's plan-time validator; this
file covers only the prompt-nudge half.)
"""

from __future__ import annotations

from bin._planner.prompt_templates import (
    CALL2_SYSTEM,
    TESTS_ENABLED_CONTRACT,
    render_call1_user,
)



def _render_call1(step: str = "implplan") -> str:
    """Render a minimal Call-1 user prompt (empty wrapped findings)."""
    return render_call1_user(
        slug="contract_nudge_demo",
        recon="<recon-findings>\n\n</recon-findings>",
        qa="<qa-findings>\n\n</qa-findings>",
        research="<research-findings>\n\n</research-findings>",
        qna="<qna-findings>\n\n</qna-findings>",
        lessons="<lessons-findings>\n\n</lessons-findings>",
        repo_state="(no repo-state summary provided)",
        prior_plan='{"slug": "contract_nudge_demo"}' if step == "implplan" else None,
        step=step,
    )


# --------------------------------------------------------------------------- #
# (a) tests_enabled + pytest-selector co-occurrence                            #
# --------------------------------------------------------------------------- #


def test_contract_demands_runnable_pytest_selector():
    """The contract names the field AND the runnable-selector shape."""
    assert "tests_enabled" in TESTS_ENABLED_CONTRACT
    assert "runnable pytest selector" in TESTS_ENABLED_CONTRACT
    # The two concrete selector shapes are spelled out.
    assert "path/to/test_file.py" in TESTS_ENABLED_CONTRACT
    assert "path/to/test_file.py::test_name" in TESTS_ENABLED_CONTRACT


# --------------------------------------------------------------------------- #
# (b) same-task path-membership requirement                                    #
# --------------------------------------------------------------------------- #


def test_contract_binds_selector_path_to_same_task_file_paths_touched():
    """A selector's path component must live in the SAME task's
    file_paths_touched — the phantom-selector guard, stated at authoring
    time."""
    assert "file_paths_touched" in TESTS_ENABLED_CONTRACT
    assert "SAME task" in TESTS_ENABLED_CONTRACT


# --------------------------------------------------------------------------- #
# (c) prose -> test_plan[] relocation cue                                      #
# --------------------------------------------------------------------------- #


def test_contract_relocates_prose_to_test_plan():
    """Narrative testing intent belongs in test_plan entries, never in
    tests_enabled."""
    assert "test_plan" in TESTS_ENABLED_CONTRACT
    assert "never in tests_enabled" in TESTS_ENABLED_CONTRACT


# --------------------------------------------------------------------------- #
# (d) typed gate command alternative                                           #
# --------------------------------------------------------------------------- #


def test_contract_allows_typed_gate_command():
    """Non-pytest substrate/heredoc tasks get the typed-gate-command
    escape hatch (exit 0 = pass); shape finalized by T6, named here."""
    assert "typed gate command" in TESTS_ENABLED_CONTRACT
    assert "exit 0 = pass" in TESTS_ENABLED_CONTRACT


# --------------------------------------------------------------------------- #
# (e) empty tests_enabled only for bookkeeping/doc tasks                       #
# --------------------------------------------------------------------------- #


def test_contract_restricts_empty_tests_enabled_to_bookkeeping():
    assert "bookkeeping" in TESTS_ENABLED_CONTRACT


# --------------------------------------------------------------------------- #
# the contract actually RENDERS into both calls' prompts                       #
# --------------------------------------------------------------------------- #


def test_call1_rendered_prompt_carries_contract():
    """The full contract block survives `.format(...)` into the rendered
    Call-1 user prompt (the authoring side), for both steps — the
    template embeds it unconditionally with an '(implplan only)'
    qualifier in the contract text itself."""
    rendered = _render_call1(step="implplan")
    assert TESTS_ENABLED_CONTRACT in rendered

    rendered_plan_step = _render_call1(step="plan")
    assert TESTS_ENABLED_CONTRACT in rendered_plan_step


def test_call1_rendered_prompt_carries_relocation_nudge():
    """The reasoning instructions themselves (not just the contract
    block) steer narrative intent toward test_plan entries."""
    rendered = _render_call1(step="implplan")
    assert "name runnable selectors per the contract below" in rendered
    assert "put the narrative testing intent in test_plan entries" in rendered


def test_call2_system_carries_contract_and_relocation_rule():
    """The Call-2 emission system prompt embeds the contract AND the
    transcription-time shape rule (prose -> test_plan, selector/typed
    command -> tests_enabled)."""
    rendered = CALL2_SYSTEM.format(step="implplan")
    assert TESTS_ENABLED_CONTRACT in rendered
    assert "place that prose in the task's test_plan entries" in rendered
    assert (
        "emit in tests_enabled only the runnable pytest selector "
        "or typed gate command it names" in rendered
    )


# --------------------------------------------------------------------------- #
# structural guard: contract must stay format-safe                             #
# --------------------------------------------------------------------------- #


def test_contract_contains_no_format_braces():
    """TESTS_ENABLED_CONTRACT is concatenated into templates that later go
    through `.format(...)`; a stray brace would raise KeyError/IndexError
    at render time for every planner invocation."""
    assert "{" not in TESTS_ENABLED_CONTRACT
    assert "}" not in TESTS_ENABLED_CONTRACT
