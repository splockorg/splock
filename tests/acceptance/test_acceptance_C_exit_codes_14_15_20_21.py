"""C.10 — Exit codes 14/15/20/21 from chain-overnight exit_codes module."""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


EXPECTED_CODES = {
    "EXIT_GUARDRAIL_REFUSED": 14,
    "EXIT_ORPHAN_DETECTED": 15,
    "EXIT_CHAIN_REFUSED": 20,
    "EXIT_CHAIN_FOREIGN_SENTINEL": 21,
}


def test_chain_overnight_exit_codes_14_15_20_21(repo_root):
    """C.10: guardrail + orphan + chain-refuse + foreign-sentinel codes."""
    from bin._chain_overnight import exit_codes

    actual = {n: getattr(exit_codes, n, None) for n in EXPECTED_CODES}
    mismatches = {
        n: (e, actual.get(n))
        for n, e in EXPECTED_CODES.items()
        if actual.get(n) != e
    }
    assert not mismatches, (
        f"Exit code drift on codes 14/15/20/21:\n"
        + "\n".join(f"  {n}: expected {e}, got {a}" for n, (e, a) in mismatches.items())
    )
