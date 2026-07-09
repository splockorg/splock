"""CLI entry point for `bin/chain-status` (CCOR.1 T-8 — thin stub).

Per `docs/plans/_closed/ccor_1/implplan.md` §T-8 + the orchestrator's
`call_sites` note: the L1 `chain_human_handoff` initiative will land a
fuller chain-status surface (uptime, phase totals, cost rollup, etc.)
in its own plan. This module is a deliberately minimal landing-zone
sized to render at least the PAUSED state from the
`_chain_paused.lock` sentinel + the manifest's paused-time accumulator
+ the H.9 1C running sentinel. When L1 ships, it may subsume this
module or merge against it; the surface here is kept narrow so the
merge is mechanical.

Operator usage:

    bin/chain-status --slug <slug> [--json]

Output (text mode):

    chain-status: <slug>
    state: <RUNNING | PAUSED | IDLE>
    [chain_id: <chain_id>]
    [driver_pid: <pid>]
    [driver_host: <host>]
    [chain_started_at: <iso>]
    PAUSED block (only when sentinel present):
      paused_at: <iso>
      reason: <free text | null>
      next_phase_to_enter: <int>
      this pause elapsed: HH:MM:SS
      total paused: HH:MM:SS
    Status freshness caveat (always printed).

Output (--json mode):

    {
      "schema_version": 1,
      "slug": <slug>,
      "state": "running" | "paused" | "idle",
      "chain_id": <str | null>,
      "driver_pid": <int | null>,
      "driver_host": <str | null>,
      "chain_started_at": <iso | null>,
      "paused": {                       # only present when state == "paused"
        "paused_at": <iso>,
        "reason": <str | null>,
        "next_phase_to_enter": <int>,
        "this_pause_seconds": <float>,
        "total_paused_seconds": <float>
      },
      "freshness_caveat": <str>
    }

Exit codes:

- 0 — status rendered successfully (regardless of state).
- 2 — argument-shape error / plan-dir missing / sentinel corrupt.

This CLI is READ-ONLY: no orchestrator-state mutations, no log row
emission, no sentinel writes. Per plan §C.1 the substrate's
mutation paths route through `bin/update_orchestrator`; chain-status
is a pure-read surface and intentionally has no `KNOWN_WRITERS`
registration.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from bin._chain_overnight import manifest as manifest_mod
from bin._chain_overnight import sentinel as running_sentinel
from bin._chain_pause import sentinel as pause_sentinel


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Chain state lives in the ADOPTER's plan dirs, not the plugin's. Upstream
# anchors on `parents[2]`, which under an installed plugin is the plugin cache:
# `chain-status` would then report on the plugin's own (empty) plan tree.
from bin._env_paths import plans_dir as _env_paths_plans_dir

_PLANS_DIR = _env_paths_plans_dir()

STATUS_SCHEMA_VERSION = 1

# Freshness caveat per R-sentinel-fields (qa C.4). The exact wording is
# load-bearing for the T-8 freshness-caveat test (string-match against
# both this module's help text and the user-facing output).
FRESHNESS_CAVEAT = (
    "Status is a point-in-time snapshot. next_phase_to_enter in the PAUSED "
    "block is computed at pause-request time and may be stale by one phase "
    "if the driver completed another phase between the pause request and "
    "the next pause-probe at the phase boundary. This is acceptable for "
    "operator display and not load-bearing for correctness."
)


# Closed-enum state labels (text + JSON share the same vocabulary).
STATE_RUNNING = "running"
STATE_PAUSED = "paused"
STATE_IDLE = "idle"


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the strict argparse parser.

    Per R-cli-lint-conformance (best-effort for a read-only surface):
    `allow_abbrev=False`; unknown flags refuse with argparse's standard
    exit-2 + stderr msg.
    """
    p = argparse.ArgumentParser(
        prog="bin/chain-status",
        description=(
            "Print a point-in-time status snapshot for the overnight chain "
            "rooted at docs/plans/<slug>/. Reads the H.9 1C running "
            "sentinel, the CCOR.1 pause sentinel, and the chain-sessions "
            "manifest; renders a human-friendly text block by default, or "
            "structured JSON via --json. "
            + FRESHNESS_CAVEAT
        ),
        allow_abbrev=False,
    )
    p.add_argument(
        "--slug",
        required=True,
        dest="slug",
        help="Plan slug (must exist as docs/plans/<slug>/).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Emit a structured JSON document instead of the human text "
            "block. The JSON shape is documented in the module docstring "
            "and is consumed by the future chain-status console card."
        ),
    )
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_plan_dir(slug: str) -> Path:
    """Resolve `docs/plans/<slug>/`; raise FileNotFoundError if missing."""
    plan_dir = _PLANS_DIR / slug
    if not plan_dir.exists():
        raise FileNotFoundError(f"plan directory does not exist: {plan_dir}")
    if not plan_dir.is_dir():
        raise NotADirectoryError(f"{plan_dir} is not a directory")
    return plan_dir


def _now_iso_z() -> str:
    """ISO-8601 UTC with trailing Z, second resolution."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _iso_z_to_epoch(iso_z: str) -> float:
    """Parse an ISO-8601 UTC string (trailing Z) to a POSIX epoch float.

    Mirrors `bin/_chain_overnight/manifest._iso_z_to_epoch` so we don't
    drift on the trailing-Z normalization.
    """
    normalized = iso_z.replace("Z", "+00:00") if iso_z.endswith("Z") else iso_z
    dt = datetime.datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def _format_hms(seconds: float) -> str:
    """Format `seconds` as HH:MM:SS for human display.

    Handles non-negative floats only; negative inputs are clamped to 0
    (defensive — a negative paused_at-to-now delta would indicate clock
    skew, not a real state).
    """
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


# ---------------------------------------------------------------------------
# State assembly
# ---------------------------------------------------------------------------


def _collect_state(plan_dir: Path) -> dict[str, Any]:
    """Read sentinels + manifest, return a structured state dict.

    Returns a dict with keys:
        state: "running" | "paused" | "idle"
        chain_id, driver_pid, driver_host, chain_started_at — from running
            sentinel; None if absent.
        paused: dict | None — populated only when the pause sentinel is held.

    Defensive on corrupt sentinels: a corrupt pause sentinel is treated
    as "not paused" with a stderr warning (mirrors `is_held`'s posture);
    the operator must hand-resolve via direct file inspection in that
    case.
    """
    result: dict[str, Any] = {
        "state": STATE_IDLE,
        "chain_id": None,
        "driver_pid": None,
        "driver_host": None,
        "chain_started_at": None,
        "paused": None,
    }

    # Step 1: running sentinel.
    running_payload = running_sentinel.read_sentinel(plan_dir)
    if running_payload is not None:
        result["state"] = STATE_RUNNING
        result["chain_id"] = running_payload.get("chain_id")
        result["driver_pid"] = running_payload.get("driver_pid")
        result["driver_host"] = running_payload.get("driver_host")
        result["chain_started_at"] = running_payload.get("started_at")

    # Step 2: pause sentinel. Corrupt is treated as "not paused"
    # defensively (matches `pause_sentinel.is_held`'s posture).
    try:
        pause_payload = pause_sentinel.read(plan_dir)
    except pause_sentinel.PauseSentinelCorruptError as exc:
        print(
            f"chain-status: warning — pause sentinel present but corrupt "
            f"at {exc.sentinel_path}: {exc.reason}. Reporting as not-paused; "
            f"hand-inspect the file to reconcile.",
            file=sys.stderr,
        )
        pause_payload = None

    if pause_payload is not None:
        result["state"] = STATE_PAUSED
        # Compute "this pause" elapsed from sentinel's paused_at.
        paused_at = pause_payload.get("paused_at")
        try:
            this_pause_seconds = (
                _iso_z_to_epoch(_now_iso_z()) - _iso_z_to_epoch(paused_at)
                if isinstance(paused_at, str)
                else 0.0
            )
        except (ValueError, TypeError):
            this_pause_seconds = 0.0
        # Read the cumulative paused-time accumulator. Defensive: the
        # helper returns (0.0, None) on absent/unparseable manifests.
        total_paused_seconds, paused_since = manifest_mod.read_paused_time_accumulator(
            plan_dir,
            chain_id=pause_payload.get("chain_id"),
        )
        result["paused"] = {
            "paused_at": paused_at,
            "reason": pause_payload.get("reason"),
            "next_phase_to_enter": pause_payload.get("next_phase_to_enter"),
            "this_pause_seconds": float(this_pause_seconds),
            "total_paused_seconds": float(total_paused_seconds),
            # Forensic cross-reference — useful for ops debugging when
            # the manifest accumulator and sentinel disagree on
            # paused_at/paused_since (they should be set in tandem).
            "manifest_paused_since": paused_since,
            "paused_by_pid": pause_payload.get("paused_by_pid"),
            "paused_by_host": pause_payload.get("paused_by_host"),
            "chain_id": pause_payload.get("chain_id"),
        }

    return result


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _render_text(slug: str, state: dict[str, Any]) -> str:
    """Render the human-friendly text block.

    Always ends with the freshness caveat so the operator sees it even
    when the snapshot is RUNNING / IDLE (the caveat covers
    next_phase_to_enter staleness specifically, but documenting the
    snapshot semantic at every state is per qa C.4).
    """
    lines: list[str] = []
    lines.append(f"chain-status: {slug}")
    lines.append(f"state: {state['state'].upper()}")
    if state["chain_id"]:
        lines.append(f"chain_id: {state['chain_id']}")
    if state["driver_pid"] is not None:
        lines.append(f"driver_pid: {state['driver_pid']}")
    if state["driver_host"]:
        lines.append(f"driver_host: {state['driver_host']}")
    if state["chain_started_at"]:
        lines.append(f"chain_started_at: {state['chain_started_at']}")

    paused = state.get("paused")
    if paused is not None:
        lines.append("PAUSED:")
        lines.append(f"  paused_at: {paused['paused_at']}")
        lines.append(f"  reason: {paused['reason']!r}")
        lines.append(f"  next_phase_to_enter: {paused['next_phase_to_enter']}")
        lines.append(
            f"  this pause elapsed: {_format_hms(paused['this_pause_seconds'])} "
            f"({paused['this_pause_seconds']:.0f}s)"
        )
        lines.append(
            f"  total paused: {_format_hms(paused['total_paused_seconds'])} "
            f"({paused['total_paused_seconds']:.0f}s)"
        )
        if paused.get("paused_by_pid") is not None:
            lines.append(f"  paused_by_pid: {paused['paused_by_pid']}")
        if paused.get("paused_by_host"):
            lines.append(f"  paused_by_host: {paused['paused_by_host']}")
    elif state["state"] == STATE_RUNNING:
        # Running and not paused → make legend explicit.
        lines.append("PAUSED: (no pause sentinel)")

    # Legend / freshness caveat — always printed. The string match in
    # test_status_freshness_caveat_documented.py looks for "snapshot"
    # and "next_phase_to_enter" inside this trailing block.
    lines.append("")
    lines.append(f"# {FRESHNESS_CAVEAT}")
    return "\n".join(lines) + "\n"


def _render_json(slug: str, state: dict[str, Any]) -> str:
    """Render the structured-JSON document.

    Shape documented in the module docstring. Includes the freshness
    caveat as a top-level string field so JSON consumers don't need to
    parse the text rendering to surface it.
    """
    payload: dict[str, Any] = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "slug": slug,
        "state": state["state"],
        "chain_id": state["chain_id"],
        "driver_pid": state["driver_pid"],
        "driver_host": state["driver_host"],
        "chain_started_at": state["chain_started_at"],
        "freshness_caveat": FRESHNESS_CAVEAT,
    }
    paused = state.get("paused")
    if paused is not None:
        # Surface a JSON-stable subset of the paused dict; forensic
        # cross-reference fields stay nested under "paused" as well.
        payload["paused"] = {
            "paused_at": paused["paused_at"],
            "reason": paused["reason"],
            "next_phase_to_enter": paused["next_phase_to_enter"],
            "this_pause_seconds": paused["this_pause_seconds"],
            "total_paused_seconds": paused["total_paused_seconds"],
            "manifest_paused_since": paused.get("manifest_paused_since"),
            "paused_by_pid": paused.get("paused_by_pid"),
            "paused_by_host": paused.get("paused_by_host"),
            "chain_id": paused.get("chain_id"),
        }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Top-level CLI entry; returns exit code.

    Returns:
        0 — status rendered (regardless of state).
        2 — argument-shape error, plan-dir missing, or unrecoverable
            sentinel read failure.
    """
    logging.basicConfig(
        level=os.environ.get("CHAIN_STATUS_LOG_LEVEL", "INFO"),
        format="%(asctime)s [chain-status] %(levelname)s %(message)s",
    )

    parser = _build_parser()
    args = parser.parse_args(argv)

    # Step 1: resolve plan_dir.
    try:
        plan_dir = _resolve_plan_dir(args.slug)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"chain-status: {exc}", file=sys.stderr)
        return 2

    # Step 2: collect state + render.
    try:
        state = _collect_state(plan_dir)
    except Exception as exc:  # noqa: BLE001 — defensive top-level guard
        print(f"chain-status: failed to read chain state: {exc}", file=sys.stderr)
        return 2

    if args.json_output:
        sys.stdout.write(_render_json(args.slug, state))
    else:
        sys.stdout.write(_render_text(args.slug, state))
    return 0


if __name__ == "__main__":  # pragma: no cover — module entry
    sys.exit(main(sys.argv[1:]))
