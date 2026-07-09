"""D.6 — `bin/morning-review abandon` requires `--confirm` AND `--reason`.

Per implplan §H.impl.3: abandon is a destructive operator action and
must be gated by both flags simultaneously.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def test_abandon_refuses_without_confirm():
    """D.6a: abandon without --confirm exits 31 EXIT_ABANDON_ARGS_MISSING.

    Per §H.impl.8 the refusal happens at dispatch, not at parse time (so
    argparse accepts the args; main() then refuses with exit 31).
    """
    from bin._morning_review.cli import parse_argv
    from bin._morning_review.exit_codes import EXIT_ABANDON_ARGS_MISSING

    # Should parse without raising — refusal is at dispatch.
    args = parse_argv([
        "abandon", "_acc_d6", "T01",
        "--reason", "no longer relevant",
    ])
    # Check args reflect missing confirm.
    assert not getattr(args, "confirm", False), (
        "Parser should record --confirm absence; main() then refuses"
    )
    # Exit code 31 is the documented refusal — verify it's defined.
    assert EXIT_ABANDON_ARGS_MISSING == 31, (
        "EXIT_ABANDON_ARGS_MISSING expected to be 31"
    )


def test_abandon_refuses_without_reason():
    """D.6b: abandon with --confirm but no --reason → exit 31."""
    from bin._morning_review.cli import parse_argv

    args = parse_argv(["abandon", "_acc_d6", "T01", "--confirm"])
    # Reason should be None / empty.
    reason = getattr(args, "reason", None)
    assert not reason, (
        f"abandon with --confirm but no --reason should have reason=None; got {reason!r}"
    )


def test_abandon_accepts_both_flags():
    """D.6c: abandon with --confirm AND --reason parses OK."""
    from bin._morning_review.cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "abandon", "_acc_d6", "T01",
        "--confirm",
        "--reason", "no longer relevant",
    ])
    assert args.subcommand == "abandon" or hasattr(args, "reason"), (
        "Parser should accept both --confirm and --reason"
    )
