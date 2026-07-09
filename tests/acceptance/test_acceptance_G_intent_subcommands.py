"""G.1 — `bin/intent` 6 subcommands parse + dispatch in local-JSONL-only mode.

Per inventory §1.6: bin/intent operates against local JSONL when the §P
DB migration hasn't been run. MySQL writes fall back to sync_pending.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


# 6 subcommands per quickstart §P intent registry.
INTENT_SUBCOMMANDS = ["check", "register", "list", "complete", "pivot", "doctor"]


def test_intent_parser_accepts_all_six_subcommands():
    """G.1: each documented bin/intent subcommand parses."""
    from bin._intent.cli import build_parser

    parser = build_parser()
    refused: list[tuple[str, str]] = []
    for sub in INTENT_SUBCOMMANDS:
        try:
            parser.parse_args([sub, "--help"])
        except SystemExit as exc:
            if exc.code != 0:
                refused.append((sub, f"exit {exc.code}"))
    assert not refused, (
        f"bin/intent parser did not accept: {refused}\n"
        f"Available: dir = {[s for s in INTENT_SUBCOMMANDS if (s, '') not in refused]}"
    )
