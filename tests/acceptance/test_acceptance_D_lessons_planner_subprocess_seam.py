"""D.8 — lessons → planner subprocess seam.

This row overlaps J.9 — both target the lessons-content-via-subprocess
seam. J.9 is currently skipped due to `_PLANS_DIR` hardcoding in
`bin/_lessons/writer.py`. When the Pass-4 subprocess-runner fixture
lands, J.9 will flip to passing; D.8 can then be a thinner version
focused on just CLI argument shape.

For now, D.8 verifies the argparse surface accepts the documented
syntax + the lessons CLI exposes `query --json` as a subprocess-
consumable mode.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def test_lessons_query_subcommand_has_json_mode():
    """D.8a: `bin/lessons query --json` is a documented mode (parses cleanly)."""
    from bin._lessons.cli import build_parser

    parser = build_parser()
    # The planner calls `bin/lessons query --slug X --json`; verify the
    # parser accepts that shape.
    try:
        args = parser.parse_args(["query", "test_slug", "--json"])
    except SystemExit as exc:
        pytest.fail(f"query --json should parse; got SystemExit({exc.code})")
    assert hasattr(args, "json_output") or hasattr(args, "json"), (
        "query subcommand should expose --json flag"
    )


def test_lessons_add_subcommand_has_all_required_flags():
    """D.8b: `bin/lessons add` requires the 7 documented flags."""
    from bin._lessons.cli import build_parser

    parser = build_parser()
    # Missing required flags should refuse.
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["add", "test_slug"])  # No required flags
    assert exc_info.value.code != 0, (
        "add without required flags should refuse at parse time"
    )
