"""D.7 — `bin/morning-review` eval-trace bridge subcommands parseable.

Per Sonnet M-4: mark-for-eval / label-score / retire-case are the §J
eval-trace bridge actions; they didn't have unit coverage and weren't
in my original 8-subcommand count.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


EVAL_TRACE_SUBCOMMANDS = ["mark-for-eval", "label-score", "retire-case"]


def test_morning_review_eval_trace_subcommands_parse():
    """D.7: each eval-trace bridge subcommand is registered + parseable."""
    from bin._morning_review.cli import build_parser

    parser = build_parser()
    refused: list[tuple[str, str]] = []
    for sub in EVAL_TRACE_SUBCOMMANDS:
        try:
            parser.parse_args([sub, "--help"])
        except SystemExit as exc:
            if exc.code != 0:
                refused.append((sub, f"exit {exc.code}"))
    assert not refused, (
        f"Eval-trace bridge subcommands not accepted by parser: {refused}"
    )
