"""D.5 — `bin/morning-review` 8 operator subcommands + 2 internal parse + dispatch.

Per quickstart morning-review CLI row; implplan §H.impl.3.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


# 8 operator subcommands per the docs:
OPERATOR_SUBCOMMANDS = [
    "list", "show", "reactivate", "route-outstanding",
    "route-marker", "abandon", "acknowledge", "gc",
]


def test_morning_review_parser_accepts_all_operator_subcommands():
    """D.5: each of 8 documented operator subcommands is parseable."""
    from bin._morning_review.cli import build_parser

    parser = build_parser()
    accepted: list[str] = []
    refused: list[tuple[str, str]] = []
    for sub in OPERATOR_SUBCOMMANDS:
        try:
            # Just try to parse the subcommand with minimum-viable args
            # — different subcommands require different positional args,
            # so we use a generic invocation that exercises the parser
            # without requiring real arg combinations.
            parser.parse_args([sub, "--help"])
        except SystemExit as exc:
            # --help triggers SystemExit(0); that means the parser
            # recognized the subcommand. Anything else means it didn't.
            if exc.code == 0:
                accepted.append(sub)
            else:
                refused.append((sub, str(exc)))
        except Exception as exc:
            refused.append((sub, type(exc).__name__))
    assert not refused, (
        f"morning-review parser did not accept subcommands: {refused}"
    )
    assert sorted(accepted) == sorted(OPERATOR_SUBCOMMANDS)
