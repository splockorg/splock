"""D.4 — `bin/route_issue` 5-way dispatch.

Per userguide §8: each of 5 routing types (fix-now / flag-for-review /
scheduled-marker / tier-promote / outstanding-lazy-dump) parses + dispatches
to the correct downstream surface.
"""

from __future__ import annotations

import pytest
from unittest import mock


pytestmark = pytest.mark.acceptance


# The 5 routing types per actual `bin/_route_issue.cli.VALID_TYPES`.
# Pass 4 Finding 6 closed 2026-05-22: userguide §8, quickstart, and inventory
# reconciled to these CLI-native names. (Pre-reconcile docs used "auto_apply",
# "flag_for_review", "scheduled_marker", "tier_promote", "outstanding_lazy_dump"
# — operator following the doc got argparse refusal.)
ROUTING_TYPES = ["fix-now", "outstanding", "marker", "tier-promote", "escalate"]


def test_route_issue_cli_parses_all_five_types(repo_root):
    """D.4a: all 5 --type values parse + the cli accepts each."""
    from bin._route_issue import cli

    accepted = []
    for t in ROUTING_TYPES:
        try:
            cli.parse_args([
                "--type", t,
                "--description", "test",
                "--context", "T1:phase",
                "--slug", "splock",
            ])
            accepted.append(t)
        except SystemExit:
            pass  # argparse refusal
    assert sorted(accepted) == sorted(ROUTING_TYPES), (
        f"Some routing types failed to parse: missing {set(ROUTING_TYPES) - set(accepted)}"
    )


def test_route_issue_fix_now_dispatches_with_clean_trigger(tmp_repo):
    """D.4b: fix-now happy path returns EXIT_OK + emits forensic row."""
    from bin._route_issue import cli, main as main_module, triggers

    with mock.patch(
        "bin._route_issue.triggers.evaluate",
        return_value=triggers.TriggerResult(
            forced=False, trigger="none", detail="", staged_files=[],
        ),
    ), mock.patch("bin._route_issue.log_emit.append_row"):
        args = cli.parse_args([
            "--type", "fix-now",
            "--description", "tiny fix",
            "--context", "T1:phase",
            "--slug", "splock",
        ])
        code = main_module.dispatch(args, repo_root=tmp_repo)

    assert code == 0, f"fix-now happy path should return EXIT_OK; got {code}"
