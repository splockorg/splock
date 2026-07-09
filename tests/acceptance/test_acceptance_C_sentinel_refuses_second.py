"""C.1 — `bin/chain-overnight` refuses second concurrent invocation against same slug.

Per inventory:
- Source: userguide §6.2 + plan §A H.9 1C refuse-second sentinel.
- Predecessor: tmp_slug_dir with existing `_chain_running.lock` sentinel
  written via the canonical `acquire()` API.
- Expected outcome: second `acquire()` returns AcquireResult with
  `status == "denied"` + the original sentinel content is preserved.
"""

from __future__ import annotations

import json
import os
import pytest


pytestmark = pytest.mark.acceptance


def test_sentinel_refuses_second_chain_against_same_slug(tmp_slug_dir):
    """C.1: a second concurrent chain against the same slug refuses (status=denied)."""
    from bin._chain_overnight import sentinel as sentinel_mod
    from bin._chain_overnight import exit_codes

    # Acquire #1 — should succeed.
    first = sentinel_mod.acquire(
        plan_dir=tmp_slug_dir,
        chain_id="chain_2026-05-22T12:00:00Z_existing0",
        driver_pid=os.getpid(),
        driver_host="test-host",
        wall_clock_cap_seconds=43200,
        started_at="2026-05-22T12:00:00Z",
    )
    assert first.status == "acquired", (
        f"First acquire should succeed; got status={first.status}"
    )

    sentinel_path = sentinel_mod.sentinel_path(tmp_slug_dir)
    assert sentinel_path.exists(), "Sentinel file not created by first acquire"
    original_payload = json.loads(sentinel_path.read_text(encoding="utf-8"))

    # Acquire #2 — should be denied.
    second = sentinel_mod.acquire(
        plan_dir=tmp_slug_dir,
        chain_id="chain_2026-05-22T12:05:00Z_second000",
        driver_pid=os.getpid() + 1,
        driver_host="test-host",
        wall_clock_cap_seconds=43200,
        started_at="2026-05-22T12:05:00Z",
    )
    assert second.status == "denied", (
        f"Second concurrent acquire should be denied; got status={second.status}"
    )
    assert second.live_chain_id == first.live_chain_id or \
        second.live_chain_id == "chain_2026-05-22T12:00:00Z_existing0", (
        f"Denied AcquireResult should surface live chain_id; got "
        f"live_chain_id={second.live_chain_id}"
    )

    # Original sentinel file must be intact (refusal is non-destructive).
    on_disk = json.loads(sentinel_path.read_text(encoding="utf-8"))
    assert on_disk == original_payload, (
        "Refusal must not modify the existing sentinel; "
        f"before={original_payload!r} after={on_disk!r}"
    )

    # Confirm the closed-enum exit code is allocated and matches inventory C.10.
    assert exit_codes.EXIT_CHAIN_REFUSED == 20, (
        "EXIT_CHAIN_REFUSED expected to be 20 per inventory C.10"
    )
