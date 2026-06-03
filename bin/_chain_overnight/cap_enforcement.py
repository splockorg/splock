"""Wall-clock cap enforcement for the chain driver.

Per implplan §A.impl.5 (lines 561-595) + plan §A.7 criteria 2-3 + plan
§I.2.

**Wall-clock cap** (`OVERNIGHT_WALL_CLOCK_SECONDS`, default 28800):
- Pre-Sonnet-spawn check (Hole H.10 resolution): if `now() +
  OVERNIGHT_SONNET_ESTIMATED_SECONDS > deadline`, halt the retry loop
  before spawning. Default Sonnet estimate 5 min.
- signal.alarm backstop in case per-phase timeout misses.

Cap-from-manifest discipline (Finding 1): wall-clock cap is read from
`_chain_sessions.json` (where the driver stamps it at chain start).
Env vars are inputs to that initial write; subsequent checks read from
the manifest, NOT live env. This is what prevents the v2.66+ runtime-
tunable propagation gap from affecting in-flight chains. Operator
override (mid-chain) is documented at A.impl.10 #1 (RATIFIED 2026-05-21
plan §H.5 default halt-and-wait).

Cost-cap arm retired in `delete_usage_caps` (2026-05-23) — subscription
mode removes per-call budget enforcement. `CapVerdict` retains only the
wall-clock fields. `check_cost_cap` and `resolve_cost_cap_usd` were
removed from the module surface in that slug; downstream code paths
have been collapsed accordingly.

CCOR.1 paused-time integration (per R-cap-injection):
`check_wall_clock_cap` now accepts an optional paused-time accumulator
pair — `total_paused_seconds` (completed pauses) and
`paused_since_epoch` (active-pause start, if currently paused). When
either is non-zero, the function subtracts paused time from elapsed so
the cap doesn't burn wall-clock budget while the operator is paused.
The accumulator is sourced from
`bin/_chain_overnight/manifest.py::read_paused_time_accumulator` and
threaded through `phase_spawn.py::precheck_caps`. Math is detailed in
`check_wall_clock_cap`'s docstring.

CCOR.1 implicit-cost-freeze invariant (per R-cost-implicit-freeze):
Even though the cost-cap arm has been retired, the freeze-during-pause
discipline must remain coherent for any future re-introduction of cost
accounting. The invariant is:

    `CapVerdict.next_estimate_usd` is *time-independent* — it is
    resolved at precheck time from settings (or a static per-phase
    estimate), NOT computed from wall-clock elapsed. Because no new
    spawns complete during a pause, `read_cumulative_cost` returns the
    same value across a pause window, so any cost cap is *implicitly
    frozen* without explicit pause-related arithmetic.

If a future caller reintroduces a `check_cost_cap`-style helper, it
MUST preserve this property: do not derive `next_estimate_usd` from
wall-clock elapsed, and do not consult paused-time fields. The
implicit-freeze invariant only holds while next_estimate_usd stays
time-independent. T-3's `test_cost_cap_unchanged.py` guards both
the current absence of `check_cost_cap` and the time-independence of
`CapVerdict.next_estimate_usd`.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)


# Defaults per §A.impl.5
DEFAULT_WALL_CLOCK_SECONDS = 28800  # 8 hours
DEFAULT_SONNET_ESTIMATED_SECONDS = 300  # 5 minutes


@dataclass(frozen=True)
class CapVerdict:
    """Result of a cap check.

    `verdict == "pass"`: under cap; spawn allowed.
    `verdict == "wall_clock_exceeded"`: halt; now() + estimate > deadline.
    """

    verdict: Literal["pass", "wall_clock_exceeded"]
    next_estimate_usd: float = 0.0
    seconds_remaining: float = 0.0
    estimated_phase_seconds: float = 0.0


def resolve_wall_clock_cap() -> int:
    """Resolve wall-clock cap from env, falling back to default."""
    raw = os.environ.get("OVERNIGHT_WALL_CLOCK_SECONDS")
    if raw is None:
        return DEFAULT_WALL_CLOCK_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning(
            "invalid OVERNIGHT_WALL_CLOCK_SECONDS=%r; using default %s",
            raw, DEFAULT_WALL_CLOCK_SECONDS,
        )
        return DEFAULT_WALL_CLOCK_SECONDS


def resolve_sonnet_estimated_seconds() -> int:
    """Resolve Sonnet pre-spawn estimate (used by phase_spawn wall-clock check)."""
    raw = os.environ.get("OVERNIGHT_SONNET_ESTIMATED_SECONDS")
    if raw is None:
        return DEFAULT_SONNET_ESTIMATED_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_SONNET_ESTIMATED_SECONDS


def check_wall_clock_cap(
    *,
    chain_started_at_epoch: float,
    wall_clock_cap_seconds: int,
    estimated_phase_seconds: int | None = None,
    now: float | None = None,
    total_paused_seconds: float = 0.0,
    paused_since_epoch: float | None = None,
) -> CapVerdict:
    """Wall-clock cap check.

    Returns "wall_clock_exceeded" if `now + estimated_phase_seconds`
    exceeds the *effective* deadline (`chain_started_at_epoch + cap +
    paused_time_to_subtract`). Equivalently — and more legibly — the
    function computes `effective_elapsed` and compares
    `effective_elapsed + estimated_phase_seconds` against the cap:

        effective_elapsed = (now - chain_started_at_epoch)
                            - total_paused_seconds
                            - (now - paused_since_epoch
                               if paused_since_epoch else 0.0)
        effective_elapsed = max(0.0, effective_elapsed)  # clamp

    `estimated_phase_seconds`: caller may pass per-phase estimate;
    defaults to `OVERNIGHT_SONNET_ESTIMATED_SECONDS` (5 min) as the
    pre-spawn check per Hole H.10 resolution.

    CCOR.1 paused-time arms (per R-cap-injection):

    - `total_paused_seconds`: cumulative seconds across *completed*
      pause windows (`stamp_pause_end` increments this each time the
      operator resumes). Default 0.0 preserves pre-CCOR.1 semantics.
    - `paused_since_epoch`: POSIX epoch when the *active* pause began,
      or None if the chain is not currently paused. When non-None, the
      function subtracts `(now - paused_since_epoch)` as the
      active-pause delta. Default None preserves pre-CCOR.1 semantics.

    Both inputs are sourced by `phase_spawn.py::precheck_caps` from
    `manifest.read_paused_time_accumulator` (which returns the ISO-Z
    string for `paused_since`; the caller converts to epoch before
    passing).

    Clamping (`max(0.0, effective_elapsed)`): if paused time exceeds
    elapsed (e.g., clock skew, malformed manifest, or a stamp_pause_end
    delta computed against an out-of-band resumed_at), the effective
    elapsed clamps to zero rather than going negative — a negative
    elapsed would silently extend the deadline by clock-skew amount.
    """
    if now is None:
        now = time.time()
    if estimated_phase_seconds is None:
        estimated_phase_seconds = resolve_sonnet_estimated_seconds()

    # Raw wall-clock elapsed since chain start.
    raw_elapsed = now - chain_started_at_epoch
    # Active-pause delta (None means no active pause).
    active_pause_delta = (
        (now - paused_since_epoch) if paused_since_epoch is not None else 0.0
    )
    # Subtract completed + active paused time; clamp at zero to defend
    # against malformed accumulators or clock skew.
    effective_elapsed = max(
        0.0, raw_elapsed - total_paused_seconds - active_pause_delta
    )
    # Effective deadline relative to `now` — i.e., how many real-clock
    # seconds remain before we run out of usable budget.
    remaining = wall_clock_cap_seconds - effective_elapsed
    if effective_elapsed + estimated_phase_seconds > wall_clock_cap_seconds:
        return CapVerdict(
            verdict="wall_clock_exceeded",
            seconds_remaining=max(0.0, remaining),
            estimated_phase_seconds=float(estimated_phase_seconds),
        )
    return CapVerdict(
        verdict="pass",
        seconds_remaining=max(0.0, remaining),
        estimated_phase_seconds=float(estimated_phase_seconds),
    )


__all__ = [
    "CapVerdict",
    "DEFAULT_SONNET_ESTIMATED_SECONDS",
    "DEFAULT_WALL_CLOCK_SECONDS",
    "check_wall_clock_cap",
    "resolve_sonnet_estimated_seconds",
    "resolve_wall_clock_cap",
]
