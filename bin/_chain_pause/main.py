"""CLI entry point for `bin/chain-pause` (CCOR.1 T-5).

Per implplan §T-5 + design_resolutions R-cli-arg-shape +
R-cli-lint-conformance + R-exit-codes. The operator runs:

    bin/chain-pause --slug <slug> [--reason "text"] [--chain-id <id>]

The CLI:

1. Resolves `plan_dir = docs/plans/<slug>/`; exits 2 if missing.
2. Reads the live H.9 1C sentinel `_chain_running.lock` via
   `bin._chain_overnight.sentinel.read_sentinel`; exits 2 if absent
   ("no chain to pause").
3. Liveness-probes the recorded `driver_pid` with `os.kill(pid, 0)`;
   if dead (ProcessLookupError), exits 2 with "driver dead — chain
   crashed".
4. If `--chain-id` was supplied, verifies it matches the running
   lock's chain_id; exits 2 on mismatch (defensive precision per
   R-cli-arg-shape).
5. Reads the manifest via `manifest.read_manifest` and computes
   `next_phase_to_enter` per R-sentinel-fields:
     - empty `phases` → 2
     - else `phases[-1].ended_at` set → `phases[-1].phase + 1`
     - else (mid-phase) → `phases[-1].phase`
6. Calls `pause_sentinel.acquire(...)` with the computed payload;
   on `PauseAlreadyHeldError` exits 23 (`EXIT_ALREADY_PAUSED`).
7. Emits a `chain_paused` row via `bin/_jsonl_log/writer.append_row`
   with `emitted_by="bin/chain-pause"` and event_type payload
   discriminator.
8. Prints confirmation:
   "chain <chain_id> paused at <iso>; next probe within ~5s after
   current phase boundary; use bin/chain-resume to wake"

Exit codes (per `bin/_chain_overnight/exit_codes.py`):

- 0 (`EXIT_OK`) — pause sentinel written successfully.
- 2 (`EXIT_DRIVER_CRASH`) — argument-shape error, plan-dir missing,
  running lock absent, dead driver PID, chain_id mismatch.
- 23 (`EXIT_ALREADY_PAUSED`) — pause sentinel already held.

The CLI does NOT mutate `_state.json` or `_orchestrator_log.jsonl`
directly outside of the single `append_row` call (per plan §C.1 +
R-cli-lint-conformance sole-writer discipline).
"""

from __future__ import annotations

import argparse
import datetime
import errno
import logging
import os
import socket
import sys
from pathlib import Path
from typing import Sequence

# Module imports. Each module is single-purpose; we keep import surface
# tight so the CLI's failure modes are easy to reason about.
from bin._chain_overnight import exit_codes
from bin._chain_overnight import manifest as manifest_mod
from bin._chain_overnight import sentinel as running_sentinel
from bin._chain_pause import sentinel as pause_sentinel
from bin._jsonl_log.writer import append_row


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Repo root anchor: `bin/_chain_pause/main.py` → parents[2] = repo root.
from bin._env_paths import plans_dir as _env_paths_plans_dir

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLANS_DIR = _env_paths_plans_dir()

# `emitted_by` constant. Must match the KNOWN_WRITERS entry registered
# in `bin/_jsonl_log/writers.py` exactly (per implplan §C.impl.6 layer 1).
EMITTED_BY = "bin/chain-pause"

# Event-type discriminator payload field. Per R-event-types: four new
# values are added to the closed enum in T-8's schema work; for T-5 the
# field is a free-form payload discriminator (the schema's
# StandardRow.properties.event_type is permissive at write time).
EVENT_TYPE = "chain_paused"

# Phase numbering. The chain driver's PHASE_SEQUENCE = (2, 3, 4, 5); a
# chain that has not yet completed phase 2 (phases list empty) is about
# to enter phase 2.
INITIAL_PHASE = 2


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the strict argparse parser.

    Per R-cli-lint-conformance:
    - `allow_abbrev=False` — operators cannot abbreviate `--slug` etc.
    - Unknown flags refuse with argparse's standard exit-2 + stderr msg.
    - All flags use explicit `dest=` for clarity.
    """
    p = argparse.ArgumentParser(
        prog="bin/chain-pause",
        description=(
            "Request a phase-boundary pause for the live overnight chain "
            "at <slug>. Writes _chain_paused.lock under docs/plans/<slug>/; "
            "the driver's pause-probe halts at the next phase boundary "
            "and stays parked until bin/chain-resume runs."
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
        "--reason",
        default=None,
        dest="reason",
        help=(
            "Free-text reason for the pause. Stored in the sentinel and "
            "the chain_paused log row. Optional."
        ),
    )
    p.add_argument(
        "--chain-id",
        default=None,
        dest="chain_id",
        help=(
            "Optional chain_id; when provided, must match the running "
            "lock's chain_id (defensive precision per R-cli-arg-shape). "
            "Omit to use whatever chain is live for this plan-dir."
        ),
    )
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso_z() -> str:
    """ISO-8601 UTC with trailing Z, second resolution.

    Matches the format used by `bin/_chain_overnight/sentinel.py` and the
    `bin/_chain_pause/sentinel.py` payload so timestamps round-trip
    cleanly.
    """
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _resolve_plan_dir(slug: str) -> Path:
    """Resolve `docs/plans/<slug>/`; raise FileNotFoundError if missing."""
    plan_dir = _PLANS_DIR / slug
    if not plan_dir.exists():
        raise FileNotFoundError(f"plan directory does not exist: {plan_dir}")
    if not plan_dir.is_dir():
        raise NotADirectoryError(f"{plan_dir} is not a directory")
    return plan_dir


def _is_pid_alive(pid: int) -> bool:
    """Check whether `pid` is alive on the local machine.

    Defensive: signal 0 doesn't deliver anything; it triggers the kernel's
    permission/existence check only.

    Returns:
        True if the process exists (or exists but is owned by another user
        — EPERM). False on ESRCH or any other OSError.
    """
    try:
        os.kill(pid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            # Process exists but we don't own it; treat as alive.
            return True
        return False
    return True


def _compute_next_phase_to_enter(manifest_payload: dict) -> int:
    """Derive `next_phase_to_enter` from the manifest's phases array.

    Per R-sentinel-fields:
    - empty `phases` → INITIAL_PHASE (2)
    - else `phases[-1].ended_at` is non-empty → `phases[-1].phase + 1`
    - else (still inside phases[-1]) → `phases[-1].phase`

    `next_phase_to_enter` is a forensic / display-only field; the
    freshness caveat is documented in `pause_sentinel.py`'s module
    docstring and the userguide §6.5 (T-10). It may be stale by one
    phase if the driver completes another phase between this read and
    the pause-probe at the next boundary.
    """
    phases = manifest_payload.get("phases", []) or []
    if not phases:
        return INITIAL_PHASE
    last = phases[-1]
    if not isinstance(last, dict):
        # Defensive: malformed phase row → treat as "still inside" so we
        # don't accidentally advance.
        logger.warning(
            "manifest phases[-1] is not a dict; defaulting next_phase to INITIAL_PHASE"
        )
        return INITIAL_PHASE
    current_phase = last.get("phase")
    if not isinstance(current_phase, int):
        logger.warning(
            "manifest phases[-1].phase is not an int (got %r); defaulting to INITIAL_PHASE",
            current_phase,
        )
        return INITIAL_PHASE
    ended_at = last.get("ended_at")
    if ended_at:
        # Phase completed; we are queued for the next one.
        return current_phase + 1
    # Mid-phase pause request; the queued phase IS the current one.
    return current_phase


# ---------------------------------------------------------------------------
# Log emission
# ---------------------------------------------------------------------------


def _emit_chain_paused_row(
    plan_dir: Path,
    *,
    slug: str,
    chain_id: str,
    session_id: str,
    reason: str | None,
    paused_at: str,
    next_phase_to_enter: int,
) -> None:
    """Emit a `chain_paused` row to `_orchestrator_log.jsonl`.

    Per R-event-types: event_type discriminator is `chain_paused`.
    Per R-cli-lint-conformance: this is the ONLY orchestrator-log write
    in the chain-pause CLI surface (sole-writer discipline).

    The row's `transition.from`/`transition.to` are both `wip` because
    pause is an intra-chain observability event — the chain's seven-status
    state does not change.
    """
    reason_text = f"chain_paused next_phase_to_enter={next_phase_to_enter}"
    if reason:
        reason_text += f"; operator_reason={reason!r}"
    row: dict = {
        "session_id": session_id,
        "plan_slug": slug,
        "chain_id": chain_id,
        "task_id": None,
        "transition": {"from": "wip", "to": "wip"},
        "mode_at_transition": {"overnight": True, "guardrail": False},
        "reason": reason_text,
        # Payload-level discriminator (per R-event-types). The full enum
        # surface is closed in T-8's schema update.
        "event_type": EVENT_TYPE,
        # Additive payload field — operator inspection convenience.
        "next_phase_to_enter": next_phase_to_enter,
    }
    if reason is not None:
        row["operator_reason"] = reason
    append_row(plan_dir, row, emitted_by=EMITTED_BY)


def _operator_session_id() -> str:
    """Derive a session_id for the operator-CLI invocation.

    Per `bin/_update_orchestrator/log_emit.py::session_id`: reads
    `$CLAUDE_SESSION_ID` if set (interactive Claude Code session), else
    falls back to `sess_00000000` (the placeholder used by other CLI
    surfaces when running outside a Claude session — e.g., a raw shell
    invocation of `bin/chain-pause`).
    """
    return os.environ.get("CLAUDE_SESSION_ID") or "sess_00000000"


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Top-level CLI entry; returns exit code.

    Returns:
        0 on successful pause-sentinel acquire + log row emission.
        2 on any argument-shape, plan-dir, running-lock, or liveness
          refusal.
        23 if the pause sentinel is already held.
    """
    logging.basicConfig(
        level=os.environ.get("CHAIN_PAUSE_LOG_LEVEL", "INFO"),
        format="%(asctime)s [chain-pause] %(levelname)s %(message)s",
    )

    parser = _build_parser()
    # argparse refuses unknown flags with exit 2 + stderr message; no
    # extra defensive handling needed here.
    args = parser.parse_args(argv)

    # Step 1: resolve plan_dir.
    try:
        plan_dir = _resolve_plan_dir(args.slug)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"chain-pause: {exc}", file=sys.stderr)
        return exit_codes.EXIT_DRIVER_CRASH

    # Step 2: read the running sentinel.
    running_payload = running_sentinel.read_sentinel(plan_dir)
    if running_payload is None:
        print(
            f"chain-pause: no chain to pause; "
            f"_chain_running.lock not present at {plan_dir}. "
            f"If a chain is supposedly live, check bin/chain-status.",
            file=sys.stderr,
        )
        return exit_codes.EXIT_DRIVER_CRASH

    live_chain_id = running_payload.get("chain_id")
    if not isinstance(live_chain_id, str):
        print(
            f"chain-pause: running lock present at {plan_dir} but missing "
            f"or malformed chain_id field; refusing to pause an "
            f"unidentifiable chain.",
            file=sys.stderr,
        )
        return exit_codes.EXIT_DRIVER_CRASH

    # Step 3: optional --chain-id precision check.
    if args.chain_id is not None and args.chain_id != live_chain_id:
        print(
            f"chain-pause: --chain-id={args.chain_id!r} does not match "
            f"the running lock's chain_id={live_chain_id!r}; refusing to "
            f"pause a non-matching chain.",
            file=sys.stderr,
        )
        return exit_codes.EXIT_DRIVER_CRASH

    # Step 4: liveness probe on the driver PID. The sentinel may have
    # been left behind by a crashed driver; pausing a dead chain is
    # always a user error — the operator likely wants the `--release-lock`
    # recovery flow, not pause.
    driver_pid = running_payload.get("driver_pid")
    if not isinstance(driver_pid, int):
        print(
            f"chain-pause: running lock at {plan_dir} has malformed "
            f"driver_pid={driver_pid!r}; refusing to pause.",
            file=sys.stderr,
        )
        return exit_codes.EXIT_DRIVER_CRASH

    driver_host = running_payload.get("driver_host")
    local_host = socket.gethostname()
    if isinstance(driver_host, str) and driver_host == local_host:
        # Same-host check; we can verify PID liveness directly.
        if not _is_pid_alive(driver_pid):
            print(
                f"chain-pause: driver_pid={driver_pid} is dead — chain "
                f"crashed during execution. The pause sentinel is "
                f"meaningless without a driver to honor it. "
                f"Use bin/chain-overnight --release-lock <chain_id> "
                f"<slug> to clear the running lock if you want a clean "
                f"reset.",
                file=sys.stderr,
            )
            return exit_codes.EXIT_DRIVER_CRASH
    else:
        # Cross-host: best-effort warning, but we still allow the pause
        # since the operator may know the remote chain is alive. Surface
        # the limitation so the operator can investigate if needed.
        logger.warning(
            "driver_host=%r differs from local_host=%r; cannot verify "
            "PID liveness across hosts. Proceeding with pause request.",
            driver_host, local_host,
        )

    # Step 5: read the manifest to compute next_phase_to_enter.
    try:
        manifest_payload = manifest_mod.read_manifest(plan_dir)
    except (
        manifest_mod.ManifestNotFoundError,
        manifest_mod.ManifestSchemaError,
    ) as exc:
        # The running lock exists but the manifest is unreadable — odd
        # state. Refuse the pause so the operator investigates.
        print(
            f"chain-pause: cannot read manifest at {plan_dir}: {exc}. "
            f"The running lock is present but the manifest is missing or "
            f"malformed; investigate the chain state before pausing.",
            file=sys.stderr,
        )
        return exit_codes.EXIT_DRIVER_CRASH

    next_phase = _compute_next_phase_to_enter(manifest_payload)

    # Step 6: acquire the pause sentinel via O_EXCL. Note: per T-4
    # implementer's hand-off, paused_by_pid is the CLI's own PID
    # (os.getpid()), not the driver's — this lets forensic readers
    # distinguish the operator-CLI invocation from the driver.
    paused_at = _now_iso_z()
    try:
        pause_sentinel.acquire(
            plan_dir,
            chain_id=live_chain_id,
            paused_at=paused_at,
            paused_by_pid=os.getpid(),
            paused_by_host=local_host,
            reason=args.reason,
            next_phase_to_enter=next_phase,
        )
    except pause_sentinel.PauseAlreadyHeldError as exc:
        live_id = exc.live_chain_id if exc.live_chain_id else "<unreadable>"
        print(
            f"chain-pause: pause sentinel already held for chain "
            f"{live_id!r} at {exc.sentinel_path}. Either a prior "
            f"bin/chain-pause is still in effect, or the sentinel "
            f"orphaned from an earlier session; use bin/chain-resume to "
            f"clear (or bin/chain-overnight --release-lock for a forensic "
            f"reset).",
            file=sys.stderr,
        )
        return exit_codes.EXIT_ALREADY_PAUSED
    except pause_sentinel.PauseSentinelCorruptError as exc:
        # The sentinel file exists in a corrupt state; this is a hand-
        # resolve situation. The acquire() implementation surfaces the
        # corrupt-state diagnostic via the exception; we re-print and
        # exit driver-crash so the operator runs cleanup.
        print(
            f"chain-pause: pause sentinel exists but is corrupt at "
            f"{exc.sentinel_path}: {exc.reason}. Manually inspect/remove "
            f"the file before retrying.",
            file=sys.stderr,
        )
        return exit_codes.EXIT_DRIVER_CRASH

    # Step 7: emit the chain_paused orchestrator_log row. Sole-writer
    # discipline (per R-cli-lint-conformance) — this is the ONE write to
    # the JSONL that the chain-pause CLI performs.
    session_id = _operator_session_id()
    try:
        _emit_chain_paused_row(
            plan_dir,
            slug=args.slug,
            chain_id=live_chain_id,
            session_id=session_id,
            reason=args.reason,
            paused_at=paused_at,
            next_phase_to_enter=next_phase,
        )
    except Exception as exc:  # noqa: BLE001 — log row failure is non-fatal but visible
        # The sentinel is already written; log-row emission failure is
        # forensic-only. Surface to stderr but still exit OK — the pause
        # request itself succeeded and the driver will honor the
        # sentinel.
        logger.error(
            "chain_paused log row emission failed: %s; pause sentinel is "
            "in place and will be honored, but the audit row is missing.",
            exc,
        )

    # Step 8: operator confirmation.
    print(
        f"chain {live_chain_id} paused at {paused_at}; "
        f"next probe within ~5s after current phase boundary; "
        f"queued for phase {next_phase}; "
        f"use bin/chain-resume to wake."
    )
    return exit_codes.EXIT_OK


if __name__ == "__main__":  # pragma: no cover — module entry
    sys.exit(main(sys.argv[1:]))
