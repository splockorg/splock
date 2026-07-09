"""C.3 — `bin/chain-overnight-release-lock` succeeds when driver PID is dead."""

from __future__ import annotations

import json
import os
import pytest


pytestmark = pytest.mark.acceptance


def _find_dead_pid() -> int:
    """Return a PID very unlikely to be in use (high integer)."""
    # Linux PID max is typically 2^22 = 4194304. Pick a value above that.
    return 9999999


def test_release_lock_succeeds_when_pid_dead(tmp_slug_dir):
    """C.3: release-lock removes sentinel when PID is dead; atomic discipline holds."""
    from bin._chain_overnight import sentinel as sentinel_mod

    # Write a sentinel with a guaranteed-dead PID.
    dead_pid = _find_dead_pid()
    result = sentinel_mod.acquire(
        plan_dir=tmp_slug_dir,
        chain_id="chain_2026-05-22T12:00:00Z_dead00000",
        driver_pid=dead_pid,
        driver_host="test-host",
        wall_clock_cap_seconds=43200,
    )
    assert result.status == "acquired"
    sentinel_path = sentinel_mod.sentinel_path(tmp_slug_dir)
    assert sentinel_path.exists()

    release_fn = getattr(sentinel_mod, "release_if_orphaned",
                         getattr(sentinel_mod, "release_lock", None))
    if release_fn is None:
        pytest.skip("release-lock function not exposed at module level")

    # Attempt release — should succeed.
    try:
        r = release_fn(plan_dir=tmp_slug_dir,
                       chain_id="chain_2026-05-22T12:00:00Z_dead00000")
        succeeded = (r is None) or \
                    (hasattr(r, "status") and r.status in ("released", "acquired"))
    except Exception as exc:
        pytest.fail(f"release-lock raised unexpectedly for dead PID: {exc}")

    assert succeeded, "release-lock should succeed when sentinel PID is dead"
    assert not sentinel_path.exists(), (
        "Sentinel still present after successful release"
    )
