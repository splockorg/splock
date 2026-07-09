"""C.9 — Exit codes 9/10/11 from chain-overnight exit_codes module.

Per Opus B-2: my initial inventory missed these codes; this test pins
their values so renumbering (Pass 2 Finding 1) doesn't silently break.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


EXPECTED_CODES = {
    "EXIT_SEALED_PATH_REFUSED": 9,
    "EXIT_PHASE_BOUNDARY_HALT": 10,
    "EXIT_WALL_CLOCK_CAP": 11,
}


def test_chain_overnight_exit_codes_9_10_11(repo_root):
    """C.9: halt + refuse codes match their documented values."""
    from bin._chain_overnight import exit_codes

    actual = {n: getattr(exit_codes, n, None) for n in EXPECTED_CODES}
    mismatches = {
        n: (e, actual.get(n))
        for n, e in EXPECTED_CODES.items()
        if actual.get(n) != e
    }
    assert not mismatches, (
        f"Exit code drift on codes 9-11:\n"
        + "\n".join(f"  {n}: expected {e}, got {a}" for n, (e, a) in mismatches.items())
    )
