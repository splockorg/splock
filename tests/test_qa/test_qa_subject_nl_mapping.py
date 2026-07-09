"""tests/test_qa/test_qa_subject_nl_mapping.py

Per qa_review_target_generalization T9 (slash-layer NL mapping +
transparency notice).

T9 generalizes `/qa` so its review subject is selectable via a
`--subject {recon,qna,research,plan}` flag (default `recon`), and the
slash command documents how operator-natural prose maps to that flag —
ORTHOGONAL to the existing redo-synonym re-run-mode mapping. This module
pins the `commands/qa.md` documentation contract for that NL mapping.

Contract asserted against `commands/qa.md`:
- documents the `--subject` flag and all four subjects
  (`recon`, `qna`, `research`, `plan`);
- documents the subject NL-mapping (e.g. "review the plan" →
  `--subject plan`) and the explicit default-to-`recon` rule;
- documents a transparency notice that names the resolved subject.

The asserted substrings match the shipped `commands/qa.md` verbatim. Do not
weaken them to accommodate a doc edit — update the command doc instead.
"""

from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMMANDS_DIR = _REPO_ROOT / "commands"
_QA_MD = _COMMANDS_DIR / "qa.md"

# The four closed subjects qa can review. Each must be reachable via the
# documented `--subject` flag.
SUBJECTS = ["recon", "qna", "research", "plan"]


def _body() -> str:
    assert _QA_MD.exists(), f"qa.md missing under {_COMMANDS_DIR}"
    return _QA_MD.read_text(encoding="utf-8")


def test_subject_flag_documented() -> None:
    """commands/qa.md documents the `--subject` flag."""
    body = _body()
    assert "--subject" in body, (
        "commands/qa.md must document the `--subject` flag (the review "
        "subject is selectable per qa_review_target_generalization T9)."
    )


def test_all_four_subjects_documented() -> None:
    """commands/qa.md enumerates the closed subject set and reaches each
    subject via `--subject <s>`."""
    body = _body()
    # The closed enum is documented as a literal brace-set in both the
    # frontmatter description analogue and the acknowledgment block.
    assert "--subject {recon,qna,research,plan}" in body, (
        "commands/qa.md must enumerate the closed subject set as "
        "`--subject {recon,qna,research,plan}`."
    )
    # And each subject must be reachable as an explicit `--subject <s>`
    # mapping target.
    for subject in SUBJECTS:
        assert f"--subject {subject}" in body, (
            f"commands/qa.md must document `--subject {subject}` as a "
            f"reachable mapping target; subject '{subject}' is missing."
        )


def test_subject_nl_mapping_documented() -> None:
    """commands/qa.md documents the prose→`--subject` NL mapping."""
    body = _body()
    # A representative prose→flag mapping rule for the plan subject.
    assert '"review the plan"' in body, (
        "commands/qa.md must document the 'review the plan' NL phrase as "
        "a `--subject plan` mapping trigger."
    )
    # The research and qna mappings round out the closed NL vocabulary.
    assert '"review the research" → `--subject research`' in body, (
        "commands/qa.md must document 'review the research' → "
        "`--subject research`."
    )
    assert '"qa the qna" / "review the qna" → `--subject qna`' in body, (
        "commands/qa.md must document the qna NL mapping → `--subject qna`."
    )


def test_default_to_recon_rule_documented() -> None:
    """commands/qa.md documents the explicit default-to-`recon` rule."""
    body = _body()
    assert "default, no subject named → `--subject recon`" in body, (
        "commands/qa.md must document the explicit default-to-recon rule: "
        "when no subject is named in the prose, the subject is "
        "`--subject recon`."
    )


def test_subject_transparency_notice_documented() -> None:
    """commands/qa.md documents a transparency notice naming the resolved
    subject."""
    body = _body()
    # Key on the stable prefix of the subject-naming notice, not the
    # cosmetic trailing parenthetical (which lists --reopen/--directive in
    # whatever spacing the doc author chose) — the meaningful contract is
    # that the notice surfaces the RESOLVED SUBJECT in the canonical
    # "Interpreted '...' as --subject <subject>" form.
    notice = "Interpreted '<original-tail-prose>' as --subject <subject>"
    assert notice in body, (
        "commands/qa.md must document a post-resolution transparency "
        "notice that names the resolved subject (the subject-naming "
        "analogue of the existing --reopen 'Interpreted ...' notice)."
    )
    # The notice must name the resolved subject on a single line.
    found_line = any(
        ("Interpreted" in line and "--subject" in line)
        for line in body.splitlines()
    )
    assert found_line, (
        "commands/qa.md must include a single-line notice containing both "
        "'Interpreted' and '--subject' (the resolved-subject transparency "
        "notice)."
    )
