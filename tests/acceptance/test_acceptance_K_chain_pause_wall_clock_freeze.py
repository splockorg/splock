"""K2 — wall-clock cap freeze math end-to-end.

Per CCOR.1 implplan §T-9 + design_resolutions R-cap-injection.

Contract:
- Pause for a measurable simulated duration (mock clock).
- Resume; `check_wall_clock_cap` reports `effective_elapsed` reduced by
  pause duration.
- A cap that would have fired without the freeze does NOT fire after
  freeze is applied.

The freeze math is the load-bearing correctness property of CCOR.1.
This test exercises the full integration: manifest accumulator +
`paused_since_epoch` arm + `check_wall_clock_cap` consumer.
"""

from __future__ import annotations

import pytest

from bin._chain_overnight import cap_enforcement


pytestmark = pytest.mark.acceptance


def test_K2_completed_pause_subtracts_from_effective_elapsed():
    """A 1000s pause window subtracts 1000s from effective elapsed.

    Chain started at T=0; cap=2000s. At T=1500, raw elapsed=1500s
    (would exceed the cap given an estimated phase of 600s). With
    1000s in `total_paused_seconds`, effective elapsed = 500s, so
    500 + 600 = 1100 < 2000 → pass.
    """
    chain_started_at_epoch = 0.0
    now = 1500.0
    cap = 2000

    # Without pause: 1500 + 600 = 2100 > 2000 → would fail.
    v_unfrozen = cap_enforcement.check_wall_clock_cap(
        chain_started_at_epoch=chain_started_at_epoch,
        wall_clock_cap_seconds=cap,
        estimated_phase_seconds=600,
        now=now,
        total_paused_seconds=0.0,
    )
    assert v_unfrozen.verdict == "wall_clock_exceeded"

    # With 1000s pause: effective_elapsed = 500, +600 = 1100 < 2000 → pass.
    v_frozen = cap_enforcement.check_wall_clock_cap(
        chain_started_at_epoch=chain_started_at_epoch,
        wall_clock_cap_seconds=cap,
        estimated_phase_seconds=600,
        now=now,
        total_paused_seconds=1000.0,
    )
    assert v_frozen.verdict == "pass", (
        f"freeze math broken: got {v_frozen.verdict} with effective_elapsed=500"
    )


def test_K2_active_pause_window_subtracts_from_effective_elapsed():
    """Active pause (paused_since_epoch set) contributes to freeze.

    Chain started T=0, paused at T=500, now is T=1500 → 1000s of
    active pause. Cap=1500s, estimate=300s.
    Without active-pause subtraction: 1500+300 = 1800 > 1500 → fail.
    With active-pause subtraction: effective=500, +300=800 < 1500 → pass.
    """
    v = cap_enforcement.check_wall_clock_cap(
        chain_started_at_epoch=0.0,
        wall_clock_cap_seconds=1500,
        estimated_phase_seconds=300,
        now=1500.0,
        total_paused_seconds=0.0,
        paused_since_epoch=500.0,
    )
    assert v.verdict == "pass"


def test_K2_end_to_end_pause_window_via_manifest(tmp_path, monkeypatch):
    """End-to-end: stamp_pause_start → stamp_pause_end accumulates
    `total_paused_seconds`; reading it through `read_paused_time_accumulator`
    feeds `check_wall_clock_cap` correctly.
    """
    from bin._chain_overnight import manifest as manifest_mod

    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    chain_id = "chain_2026-05-24T22:00:00Z_k2k2k2k2"
    slug = "test-k2-slug"

    manifest_mod.stamp_chain_start(
        plan_dir, slug=slug, chain_id=chain_id,
        chain_started_at="2026-05-24T22:00:00Z",
        wall_clock_cap_seconds=28800,
    )
    # Pause at T=+1800s (relative ISO).
    manifest_mod.stamp_pause_start(
        plan_dir, chain_id=chain_id, paused_at="2026-05-24T22:30:00Z",
    )
    # Resume 600s later.
    delta = manifest_mod.stamp_pause_end(
        plan_dir, chain_id=chain_id, resumed_at="2026-05-24T22:40:00Z",
    )
    assert delta == pytest.approx(600.0, abs=1.0)

    # Now read back the accumulator.
    total_paused, paused_since = manifest_mod.read_paused_time_accumulator(
        plan_dir,
    )
    assert total_paused == pytest.approx(600.0, abs=1.0)
    assert paused_since is None  # no active pause after stamp_pause_end

    # Cap math respects the accumulator.
    chain_start = 1716595200.0  # 2026-05-24T22:00:00Z (synthetic)
    v = cap_enforcement.check_wall_clock_cap(
        chain_started_at_epoch=chain_start,
        wall_clock_cap_seconds=3600,
        estimated_phase_seconds=300,
        now=chain_start + 2900,  # 2900s raw; 600s paused → 2300 effective
        total_paused_seconds=total_paused,
    )
    # 2300 + 300 = 2600 < 3600 → pass. Without freeze (raw 2900+300=3200<3600),
    # this test only proves the subtraction happened; the strict-fail
    # assertion lives in test_K2_completed_pause_subtracts_from_effective_elapsed.
    assert v.verdict == "pass"
    # `seconds_remaining` = cap - effective_elapsed = 3600 - 2300 = 1300.
    assert v.seconds_remaining == pytest.approx(1300.0, abs=2.0)


def test_K2_clamp_prevents_negative_elapsed():
    """If paused time > raw elapsed (clock skew / fixture artifact),
    effective_elapsed clamps to 0 — never extends the deadline silently.
    """
    v = cap_enforcement.check_wall_clock_cap(
        chain_started_at_epoch=0.0,
        wall_clock_cap_seconds=1000,
        estimated_phase_seconds=100,
        now=500.0,
        total_paused_seconds=600.0,  # exceeds raw elapsed of 500
    )
    # effective_elapsed clamps to 0; 0 + 100 = 100 < 1000 → pass.
    # seconds_remaining = 1000 - 0 = 1000 (not 1100, which would imply
    # the deadline was extended).
    assert v.verdict == "pass"
    assert v.seconds_remaining == pytest.approx(1000.0)
