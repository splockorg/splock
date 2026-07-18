"""qa prompt templates — DELIMITER_INSTRUCTION parity with planner.

Per std_command_operator_extensions TC + plan §D.3 + v2.7 §1.D: the qa
subagent's system prompt must embed the same `DELIMITER_INSTRUCTION`
constant the planner's CALL1_SYSTEM / CALL2_SYSTEM embed, preferably by
import (single source of truth) rather than via a duplicated string. After
TC extends the constant to mention `<operator-directive>`, the qa side
must carry the new wording byte-for-byte so the data-not-instructions
discipline is uniform across all subagents that consume external content.

qa_review_target_generalization T5 additionally pins: the reworded
templates reference the neutral `<subject-under-review>` delimiter (and
no longer the recon-only `<recon-findings>` tag) for the subject body,
AND — negative assertion — the planner's closed `WrapKind` enum was NOT
extended to mint that neutral label (it stays at its seven members; the
neutral delimiter is a structural tag like `<qa-rubric>`, deliberately
outside the closed external-input enum).
"""

from __future__ import annotations


from bin._planner.external_input_sanitize import DELIMITER_INSTRUCTION, WrapKind
from bin._qa.prompt_templates import QA_SYSTEM, QA_USER_TEMPLATE


def test_qa_system_prompt_embeds_delimiter_instruction_verbatim() -> None:
    """The qa system prompt must contain the planner's DELIMITER_INSTRUCTION
    constant verbatim, preferably sourced via import (single source of truth).

    Asserts byte-equality of a substring rather than reading the file —
    this guards against a regression where the qa side duplicates the
    string (which would then drift from the planner side after future
    edits)."""
    assert DELIMITER_INSTRUCTION in QA_SYSTEM, (
        "QA_SYSTEM must embed DELIMITER_INSTRUCTION verbatim to keep the "
        "data-not-instructions discipline byte-identical with the planner"
    )


def test_qa_system_prompt_mentions_operator_directive_via_delimiter_instruction() -> None:
    """After TC, DELIMITER_INSTRUCTION enumerates `<operator-directive>` —
    confirm the qa system prompt inherits that mention transitively."""
    assert "<operator-directive>" in QA_SYSTEM, (
        "QA_SYSTEM should mention <operator-directive> (transitively via "
        "DELIMITER_INSTRUCTION) so the qa subagent treats operator-directive "
        "content as data-not-instructions when it eventually receives it (TE)"
    )


def test_qa_system_prompt_sources_constant_by_import_not_duplication() -> None:
    """The qa module should import DELIMITER_INSTRUCTION from
    bin._planner.external_input_sanitize rather than redefining it.

    Asserts the import exists in the qa module's namespace — a cheap
    structural check that catches a future refactor copying the string
    inline."""
    import bin._qa.prompt_templates as qa_pt

    # The constant must be reachable via the module's namespace as the same
    # object as the planner's constant (i.e., re-exported / imported, not
    # redefined to a string literal that happens to have the same value).
    assert hasattr(qa_pt, "DELIMITER_INSTRUCTION"), (
        "bin._qa.prompt_templates should import DELIMITER_INSTRUCTION from "
        "bin._planner.external_input_sanitize for single-source-of-truth"
    )
    assert qa_pt.DELIMITER_INSTRUCTION is DELIMITER_INSTRUCTION, (
        "bin._qa.prompt_templates.DELIMITER_INSTRUCTION must be the SAME "
        "object as bin._planner.external_input_sanitize.DELIMITER_INSTRUCTION "
        "(not a redefined string literal that happens to match by value)"
    )


# ----------------------------------------------------------------------
# T5 — neutral <subject-under-review> delimiter referenced in templates
# ----------------------------------------------------------------------

def test_qa_system_prompt_references_neutral_subject_delimiter() -> None:
    """After T5, the qa system prompt must reference the neutral
    `<subject-under-review>` delimiter as the artifact-under-review
    boundary (the relabel of the old `<recon-findings>` tag)."""
    assert "<subject-under-review>" in QA_SYSTEM, (
        "QA_SYSTEM must reference the neutral <subject-under-review> "
        "delimiter so the subagent knows where the reviewed artifact is"
    )


def test_qa_templates_drop_recon_findings_tag() -> None:
    """Neither template may still reference the recon-only
    `<recon-findings>` tag — the subject delimiter is now subject-agnostic.

    (This is about the DELIMITER TAG. The format slot was renamed from
    `{recon}` to `{subject}` in T6, completing the de-recon-ification.)"""
    assert "<recon-findings>" not in QA_SYSTEM, (
        "QA_SYSTEM still references the legacy <recon-findings> tag"
    )
    assert "<recon-findings>" not in QA_USER_TEMPLATE, (
        "QA_USER_TEMPLATE still references the legacy <recon-findings> tag"
    )


def test_qa_rubric_delimiter_instruction_preserved() -> None:
    """The `<qa-rubric>` rubric delimiter (and the system prompt's
    instruction naming it as the authoritative scaffold) must survive the
    T5 reword — only the SUBJECT delimiter changed, not the rubric one."""
    assert "<qa-rubric>" in QA_SYSTEM, (
        "the <qa-rubric> delimiter instruction must be preserved through "
        "the T5 reword"
    )


# ----------------------------------------------------------------------
# T5 — negative guard: the planner WrapKind enum was NOT extended
# ----------------------------------------------------------------------

def test_planner_wrapkind_enum_unchanged_no_subject_member() -> None:
    """The neutral subject label must NOT have been minted as a new
    `WrapKind` member. The closed enum stays at its seven members; the
    `subject-under-review` delimiter is a structural tag (like
    `<qa-rubric>`), deliberately outside the closed external-input enum.

    A regression here would mean someone took the easy path of adding a
    `subject-under-review` (or `plan-findings`, etc.) WrapKind member to
    get a neutral label — defeating the 'leave the closed enum' contract.

    Count bumped 7 → 8 (2026-07-18): `eli5-subject` joined the enum as a
    provenance-named EXTERNAL-input kind (the excerpted material /eli5
    translates) — exactly what the enum exists for, per its documented
    extension process. The guarded anti-pattern (minting an INTERNAL
    structural label as a member) is unchanged and still pinned below."""
    members = set(WrapKind.__args__)  # type: ignore[attr-defined]
    assert len(members) == 8, (
        f"WrapKind must stay at 8 members; got {len(members)}: "
        f"{sorted(members)}"
    )
    assert "subject-under-review" not in members, (
        "the neutral subject label must NOT be a WrapKind member"
    )
    assert "plan-findings" not in members, (
        "no plan-findings WrapKind member should have been added"
    )
