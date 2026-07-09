"""C.11 — Foreign sentinel mid-chain → halt with code 21.

Per Opus B-2 + implplan §A.impl: if a chain encounters a sentinel that
was stamped by a different chain_id while it's running, that's the
foreign-sentinel-mid-chain case — halt with EXIT_CHAIN_FOREIGN_SENTINEL.
"""

from __future__ import annotations

import json
import os
import pytest


pytestmark = pytest.mark.acceptance


def test_foreign_sentinel_mid_chain_raises_chain_id_mismatch(tmp_slug_dir):
    """C.11: release/check with foreign chain_id surfaces SentinelChainIdMismatch."""
    from bin._chain_overnight import sentinel as sentinel_mod

    # Plant a sentinel stamped with chain A.
    chain_a = "chain_2026-05-22T12:00:00Z_chain_a01"
    result = sentinel_mod.acquire(
        plan_dir=tmp_slug_dir,
        chain_id=chain_a,
        driver_pid=os.getpid(),
        driver_host="test-host",
        wall_clock_cap_seconds=43200,
    )
    assert result.status == "acquired"

    # Now have chain B attempt to release — should surface mismatch.
    chain_b = "chain_2026-05-22T12:00:00Z_chain_b02"
    release_fn = getattr(sentinel_mod, "release_if_orphaned",
                         getattr(sentinel_mod, "release_lock", None))
    if release_fn is None:
        pytest.skip("release_if_orphaned not exposed; covered by chain-driver suite")

    surfaced = False
    try:
        release_fn(plan_dir=tmp_slug_dir, chain_id=chain_b)
    except sentinel_mod.SentinelChainIdMismatch:
        surfaced = True
    except Exception as exc:
        msg = str(exc).lower()
        surfaced = "mismatch" in msg or "foreign" in msg or "chain_id" in msg

    assert surfaced, (
        "Foreign chain_id during release should raise SentinelChainIdMismatch "
        "or surface chain_id mismatch in error"
    )
