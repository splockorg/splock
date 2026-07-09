"""C.2 — `bin/chain-overnight-release-lock` refuses when driver PID is alive."""

from __future__ import annotations

import json
import os
import pytest


pytestmark = pytest.mark.acceptance


def test_release_lock_refuses_when_pid_alive(tmp_slug_dir):
    """C.2: release-lock refuses while the named driver PID is still running."""
    from bin._chain_overnight import sentinel as sentinel_mod

    # Acquire with current process's PID — guaranteed alive.
    result = sentinel_mod.acquire(
        plan_dir=tmp_slug_dir,
        chain_id="chain_2026-05-22T12:00:00Z_alive0000",
        driver_pid=os.getpid(),
        driver_host="test-host",
        wall_clock_cap_seconds=43200,
    )
    assert result.status == "acquired"
    sentinel_path = sentinel_mod.sentinel_path(tmp_slug_dir)
    assert sentinel_path.exists()

    # Attempt release-lock — should refuse because PID is alive.
    if not hasattr(sentinel_mod, "release_if_orphaned") and \
       not hasattr(sentinel_mod, "release_lock"):
        pytest.skip("release-lock function not exposed at module level")

    release_fn = getattr(sentinel_mod, "release_if_orphaned",
                         getattr(sentinel_mod, "release_lock", None))

    # Most release APIs return a status or raise. We allow either shape.
    refused = False
    try:
        r = release_fn(plan_dir=tmp_slug_dir,
                       chain_id="chain_2026-05-22T12:00:00Z_alive0000")
        # Accept either: returns explicit status, or returns falsy.
        if hasattr(r, "status"):
            refused = r.status in ("denied", "refused", "live")
        else:
            refused = not bool(r)
    except (RuntimeError, sentinel_mod.SentinelChainIdMismatch) as exc:
        refused = "alive" in str(exc).lower() or "live" in str(exc).lower()

    assert refused, "release-lock should refuse when sentinel-stamped PID is alive"
    assert sentinel_path.exists(), "Sentinel removed despite live PID — incorrect"
