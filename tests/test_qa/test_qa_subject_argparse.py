"""qa CLI argparse — `--subject` closed-enum flag shape.

Per qa_review_target_generalization T1 (Subject enum + --subject flag,
default recon). `/qa` generalizes its review SUBJECT to a closed enum
{recon, qna, research, plan}. These tests pin the argparse-layer
contract:

1. `--subject` is a CLOSED choice — an out-of-enum value is rejected.
   The exit code is pinned EMPIRICALLY (argparse raises SystemExit(2) on
   an invalid `choices=` value; `main()` re-maps any nonzero parse exit
   to EXIT_USAGE (1) via its SystemExit handler). Both layers are pinned.
2. The default resolves to `recon` when `--subject` is omitted, so a
   no-flag invocation is byte-identical to the CLI's historical behavior.
3. Each valid enum member parses and round-trips into the namespace.

These exercise the parser surface so a future `_build_parser` refactor
keeps the flag closed (choices), defaulted (recon), and stable.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout

import pytest

from bin._qa import exit_codes
from bin._qa.main import _build_parser, main
from bin._qa.subject import ALL_SUBJECTS, DEFAULT_SUBJECT, SUBJECT_CHOICES


# ----------------------------------------------------------------------
# Closed-choice contract
# ----------------------------------------------------------------------

def test_subject_default_is_recon_when_omitted() -> None:
    """`bin/qa qa <slug>` (no --subject) → `args.subject == 'recon'`."""
    parser = _build_parser()
    args = parser.parse_args(["qa", "example_slug"])
    assert args.subject == "recon"
    assert args.subject == DEFAULT_SUBJECT


@pytest.mark.parametrize("subject", sorted(ALL_SUBJECTS))
def test_subject_accepts_each_enum_member(subject: str) -> None:
    """Every member of the closed enum parses and round-trips."""
    parser = _build_parser()
    args = parser.parse_args(["qa", "example_slug", "--subject", subject])
    assert args.subject == subject


def test_subject_choices_match_all_subjects_exactly() -> None:
    """The argparse `choices` list is exactly the closed enum (no drift
    between the parser's accepted set and `ALL_SUBJECTS`)."""
    # The choices are sourced from SUBJECT_CHOICES; confirm that tuple is
    # exactly the frozen set so the flag can never silently accept a value
    # outside the enum (or reject one inside it).
    assert frozenset(SUBJECT_CHOICES) == ALL_SUBJECTS


def test_subject_out_of_enum_rejected_at_parser_exit_2() -> None:
    """An out-of-enum `--subject` value is rejected by argparse with
    SystemExit code 2 (pinned EMPIRICALLY — argparse's standard
    invalid-choice exit). This is the raw parser-layer code, before
    `main()`'s SystemExit handler re-maps it."""
    parser = _build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["qa", "example_slug", "--subject", "bogus"])
    assert excinfo.value.code == 2, (
        "argparse emits exit 2 for an invalid choices= value; if this "
        "changes, the empirically-pinned code below must be re-derived"
    )


def test_subject_out_of_enum_through_main_maps_to_exit_usage() -> None:
    """Through `main(...)`, an out-of-enum `--subject` surfaces as
    EXIT_USAGE (1): `main` catches the argparse SystemExit(2) and maps any
    nonzero parse exit to EXIT_USAGE so the chain-driver `$?` envelope is
    uniform across the qa CLI surface."""
    err_buf = io.StringIO()
    out_buf = io.StringIO()
    with redirect_stderr(err_buf), redirect_stdout(out_buf):
        rc = main(["qa", "example_slug", "--subject", "bogus"])
    assert rc == exit_codes.EXIT_USAGE, (
        f"out-of-enum --subject should surface as EXIT_USAGE "
        f"({exit_codes.EXIT_USAGE}) through main(); got {rc}"
    )


def test_subject_empty_string_rejected() -> None:
    """An empty `--subject ''` is not a member of the enum and is
    rejected (distinct from omission, which defaults to recon)."""
    parser = _build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["qa", "example_slug", "--subject", ""])
    assert excinfo.value.code == 2
