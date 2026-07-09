"""C.8 — Exit codes 5/7/16/17/18 match documented userguide §13.3 table.

These are the most user-facing exit codes — operator reads the §13.3
table and expects these specific numerics to mean specific things.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


EXPECTED_CODES = {
    "EXIT_ATOMIC_WRITE_FAILED": 7,
    "EXIT_VERIFY_PLAN_REJECTED": 16,
    "EXIT_RETRY_EXCEEDED": 17,
    "EXIT_OPERATOR_KILLED": 18,
}


def test_chain_overnight_exit_codes_match_userguide(repo_root):
    """C.8: chain-overnight exit code constants match userguide §13.3 expected values."""
    from bin._chain_overnight import exit_codes

    actual = {
        name: getattr(exit_codes, name, None)
        for name in EXPECTED_CODES
    }
    mismatches = {
        name: (expected, actual.get(name))
        for name, expected in EXPECTED_CODES.items()
        if actual.get(name) != expected
    }
    assert not mismatches, (
        "Exit code drift between bin._chain_overnight.exit_codes and "
        "userguide §13.3:\n" +
        "\n".join(f"  {n}: expected {e}, got {a}" for n, (e, a) in mismatches.items())
    )
