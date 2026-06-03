"""CLI entry point for `bin/chain-overnight`.

Per implplan §A.impl + plan §A.3 / §A.4 / §A.5. CLI dispatch:

    python -m bin._chain_overnight.main <phase> <slug> [...]
    python -m bin._chain_overnight.main --release-lock <chain_id> <slug>

The CLI orchestrates the chain:
  1. Acquire H.9 1C sentinel (sentinel.py).
  2. Stamp `_chain_sessions.json` manifest (manifest.py).
  3. Auto-register §P intent row (auto_register.py — best-effort; §P
     not yet built).
  4. Loop phases starting from `<phase>` (default 2):
     a. Pre-spawn cap check (cap_enforcement.py).
     b. Foreign-sentinel re-check.
     c. Spawn (phase_spawn.py — invokes §D's planner OR §F's retry loop).
     d. Stamp manifest exit-time fields.
     e. Pre-stage safety net + git commit (pre_stage.py).
     f. Emit transition row (state_machine.py).
  5. On any halt: write completion summary (completion_summary.py;
     "summary write is LAST" sequencing per orchestrator §4a.4).
  6. Release sentinel.

Exit codes per A.impl.3a closed-enum registry (exit_codes.py).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import logging
import os
import signal
import socket
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from bin._chain_pause import sentinel as pause_sentinel
from bin._jsonl_log.writer import append_row as _append_log_row

from . import (
    auto_register,
    cap_enforcement,
    completion_summary,
    exit_codes,
    manifest as manifest_mod,
    phase_spawn,
    pre_stage,
    sentinel,
    state_machine,
)

logger = logging.getLogger(__name__)

# Phase sequence in chain order
PHASE_SEQUENCE: tuple[int, ...] = (2, 3, 4, 5)


# Operator-inject file basename. Single-shot — the driver's pause-probe /
# phase-spawn helper consumes (reads + deletes) the file, ensuring the
# next planner or opus-iter spawn sees the framed inject text exactly once.
# Must match `bin/_chain_resume/main.py::OPERATOR_INJECT_FILENAME`.
OPERATOR_INJECT_FILENAME = "_operator_inject.md"


# Pause-probe polling interval. Per R-poll-interval (CCOR.1 design
# resolutions): 5 seconds is a module-level constant; promotion to
# settings_registry is a one-line CCOR follow-up if field evidence
# argues for tuning. Python's `time.sleep()` honors SIGTERM (raises
# KeyboardInterrupt via the SIGTERM handler installed by
# `_install_signal_handlers`), so the probe unwinds cleanly without any
# extra signal handling at this layer.
POLL_INTERVAL_SECONDS = 5


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLANS_DIR = _REPO_ROOT / "docs" / "plans"


# ----------------------------------------------------------------------
# CLI parser
# ----------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bin/chain-overnight",
        description=(
            "Detached overnight chain driver per plan §A. Spawns a sequence "
            "of phases (/plan → /implplan → /code → /test) bounded by a "
            "wall-clock cap; emits per-phase transition log rows via §C; "
            "writes a completion summary on every exit cause."
        ),
    )
    # `starting_phase` is stored as a string so the release-lock invocation
    # form (`--release-lock <chain_id> <slug>`) can pass an arbitrary string
    # into this slot without colliding with the `type=int, choices=` validator.
    # Numeric validation happens in `_run_chain` after dispatch.
    p.add_argument(
        "starting_phase",
        nargs="?",
        help=(
            "Phase to enter at (default 2; 3-5 for --from-resume). "
            "Omit when using --release-lock."
        ),
    )
    p.add_argument(
        "slug",
        nargs="?",
        help="Plan slug (must exist as docs/plans/<slug>/).",
    )
    p.add_argument(
        "--from-resume",
        default=None,
        help="Resume a halted chain at the next unstarted phase (chain_id).",
    )
    p.add_argument(
        "--wall-clock-seconds",
        type=int,
        default=None,
        help="Override OVERNIGHT_WALL_CLOCK_SECONDS for this run.",
    )
    p.add_argument(
        "--defer-threshold",
        type=float,
        default=None,
        help="Override OVERNIGHT_TEST_DEFER_THRESHOLD.",
    )
    p.add_argument(
        "--test-max-retries",
        type=int,
        default=None,
        help="Override OVERNIGHT_TEST_MAX_RETRIES.",
    )
    # --release-lock flag: when set, accepts a chain_id value. Slug is
    # passed via the positional `slug` arg in this mode; `starting_phase`
    # is unused.
    p.add_argument(
        "--release-lock",
        default=None,
        metavar="CHAIN_ID",
        help=(
            "Release a stranded sentinel for the given chain id. "
            "Usage: bin/chain-overnight --release-lock <chain_id> <slug>"
        ),
    )
    return p


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _now_iso_z() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _now_epoch() -> float:
    return time.time()


def _resolve_plan_dir(slug: str) -> Path:
    plan_dir = _PLANS_DIR / slug
    if not plan_dir.exists():
        raise FileNotFoundError(f"plan directory does not exist: {plan_dir}")
    if not plan_dir.is_dir():
        raise NotADirectoryError(f"{plan_dir} is not a directory")
    return plan_dir


def _mint_chain_id() -> str:
    """Chain id format: `chain_<ISO>_<short-uuid>` for uniqueness + sortability."""
    iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    suffix = uuid.uuid4().hex[:8]
    return f"chain_{iso}_{suffix}"


def _driver_session_id(chain_id: str) -> str:
    """Build the driver's own session_id (separate from per-phase ids).

    Matches `^sess_[0-9a-f]{8}$` per the orchestrator-log schema.
    """
    digest = hashlib.sha1(f"driver|{chain_id}".encode("utf-8")).hexdigest()
    return f"sess_{digest[:8]}"


# ----------------------------------------------------------------------
# Operator-inject consumption (single-shot)
# ----------------------------------------------------------------------


def _consume_operator_inject_if_present(
    plan_dir: Path,
    chain_id: str,
    *,
    slug: str | None = None,
) -> str | None:
    """Read + DELETE `_operator_inject.md` if present; emit log row on consume.

    Per R-inject-wiring + R-inject-single-shot (CCOR.1 design
    resolutions). The chain-resume CLI (T-6) writes a framed inject
    body to `<plan_dir>/_operator_inject.md`; THIS helper consumes it
    by reading the body and deleting the file. Single-shot semantics
    mean a second consume returns None (the file is gone).

    Per R-inject-framing: the framed body is returned VERBATIM (HTML
    comment + `<operator-inject>...</operator-inject>` delimiters
    preserved) so the spawned subagent sees the operator-origin
    provenance contract. The spawn-prompt-build layer is responsible
    for prepending the framed block to the prompt.

    Emits a `pause_inject_consumed` orchestrator-log row when a consume
    actually happens (file was present and read). Absent-file path is
    silent — no spam in the common case.

    Args:
        plan_dir: Plan directory to look in.
        chain_id: Chain id for the log row's `chain_id` field.
        slug: Plan slug for the log row's `plan_slug` field. May be None
            if the caller does not know the slug yet (extremely rare —
            the chain driver always has the slug by the time it reaches
            a spawn site).

    Returns:
        The framed inject body (str) if the file was present and
        consumed; None if the file was absent.
    """
    target = plan_dir / OPERATOR_INJECT_FILENAME
    if not target.exists():
        return None
    try:
        body = target.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "operator-inject read failed plan_dir=%s: %s", plan_dir, exc,
        )
        return None
    # Delete BEFORE emitting the log row so a log-write failure cannot
    # leave the inject visible to a second consume on the same boundary.
    try:
        target.unlink()
    except FileNotFoundError:
        # Race: another flow consumed between our read and unlink.
        # Treat as consumed (we have the body); skip the log row to
        # avoid double-emit.
        return body
    except OSError as exc:
        logger.warning(
            "operator-inject delete failed plan_dir=%s: %s",
            plan_dir, exc,
        )
        # Surface the body anyway — the spawn site will use it; the
        # operator can hand-remove the file later.
    # Emit `pause_inject_consumed` log row (best-effort; an emit
    # failure here is forensic-only — the consume itself is durable
    # because the file is gone).
    try:
        inject_size_bytes = len(body.encode("utf-8"))
    except Exception:  # noqa: BLE001 — defensive
        inject_size_bytes = len(body)
    session_id = _driver_session_id(chain_id)
    row: dict = {
        "session_id": session_id,
        "plan_slug": slug,
        "chain_id": chain_id,
        "task_id": None,
        "transition": {"from": "wip", "to": "wip"},
        "mode_at_transition": {"overnight": True, "guardrail": False},
        "reason": (
            f"pause_inject_consumed inject_size_bytes={inject_size_bytes}"
        ),
        "event_type": "pause_inject_consumed",
        "inject_size_bytes": inject_size_bytes,
    }
    try:
        _append_log_row(plan_dir, row, emitted_by="bin/chain-overnight")
    except Exception:  # noqa: BLE001 — best-effort log emit
        logger.warning(
            "pause_inject_consumed log row emit failed plan_dir=%s",
            plan_dir, exc_info=True,
        )
    return body


def _emit_paused_lock_stale_cleared(
    plan_dir: Path,
    *,
    slug: str | None,
    chain_id: str,
) -> None:
    """Emit a `chain_paused_lock_stale_cleared` orchestrator-log row.

    Per R-finalizer-cleanup + R-release-lock-pause. Called by:
    - `_ChainFinalizer.flush()` ONLY when the pause sentinel was
      actually present at flush time (the `release_if_present` return
      value gates the emit so successful clean-exits don't spam the
      log).
    - `_run_release_lock` when `--release-lock` removed a present pause
      sentinel.

    Best-effort: a log-emit failure here is forensic-only; the sentinel
    removal itself is durable on disk.
    """
    session_id = _driver_session_id(chain_id)
    row: dict = {
        "session_id": session_id,
        "plan_slug": slug,
        "chain_id": chain_id,
        "task_id": None,
        "transition": {"from": "wip", "to": "wip"},
        "mode_at_transition": {"overnight": True, "guardrail": False},
        "reason": "chain_paused_lock_stale_cleared",
        "event_type": "chain_paused_lock_stale_cleared",
    }
    try:
        _append_log_row(plan_dir, row, emitted_by="bin/chain-overnight")
    except Exception:  # noqa: BLE001 — best-effort log emit
        logger.warning(
            "chain_paused_lock_stale_cleared log row emit failed plan_dir=%s",
            plan_dir, exc_info=True,
        )


# ----------------------------------------------------------------------
# Main entry
# ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Top-level CLI entry. Returns exit code per A.impl.3a registry."""
    logging.basicConfig(
        level=os.environ.get("OVERNIGHT_LOG_LEVEL", "INFO"),
        format="%(asctime)s [chain-overnight] %(levelname)s %(message)s",
    )

    parser = _build_parser()
    args = parser.parse_args(argv)

    # --release-lock flag path.
    # CLI form: bin/chain-overnight --release-lock <chain_id> <slug>
    # argparse maps:
    #   --release-lock=<chain_id>
    #   positional 1 → starting_phase (the slug, since release-lock uses 1 positional)
    #   positional 2 → slug (None when only one positional given)
    # We accept either shape: slug in the slug slot OR in the starting_phase slot.
    if args.release_lock is not None:
        slug: str | None = args.slug or args.starting_phase
        if slug is None:
            parser.error("--release-lock requires <slug> as a positional argument")
            return exit_codes.EXIT_DRIVER_CRASH
        args.chain_id = args.release_lock
        args.slug = slug
        return _run_release_lock(args)

    # Chain-run path requires both phase (as integer) and slug.
    if args.starting_phase is None or args.slug is None:
        parser.error("missing required positional args: <starting_phase> <slug>")
        return exit_codes.EXIT_DRIVER_CRASH  # unreachable; argparse calls sys.exit
    try:
        args.starting_phase = int(args.starting_phase)
    except (TypeError, ValueError):
        parser.error(
            f"<starting_phase> must be an integer in {PHASE_SEQUENCE}; "
            f"got {args.starting_phase!r}"
        )
        return exit_codes.EXIT_DRIVER_CRASH
    if args.starting_phase not in PHASE_SEQUENCE:
        parser.error(
            f"<starting_phase> must be in {PHASE_SEQUENCE}; "
            f"got {args.starting_phase}"
        )
        return exit_codes.EXIT_DRIVER_CRASH

    try:
        return _run_chain(args)
    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt — operator interrupted chain")
        return exit_codes.EXIT_OPERATOR_KILLED
    except Exception:  # noqa: BLE001 — log and surface as driver crash
        logger.exception("chain-overnight driver crash")
        return exit_codes.EXIT_DRIVER_CRASH


# ----------------------------------------------------------------------
# --release-lock flow
# ----------------------------------------------------------------------


def _run_release_lock(args: argparse.Namespace) -> int:
    """Per implplan §A.impl.3 lines 430-440 — --release-lock recovery.

    1. Read sentinel.
    2. Verify chain_id matches.
    3. Check whether driver_pid alive on driver_host.
    4. Log release event to _orchestrator_log.jsonl (emitted_by =
       `bin/chain-overnight --release-lock`).
    5. Remove sentinel.
    """
    try:
        plan_dir = _resolve_plan_dir(args.slug)
    except FileNotFoundError as exc:
        print(f"chain-overnight: {exc}", file=sys.stderr)
        return exit_codes.EXIT_DRIVER_CRASH

    payload = sentinel.read_sentinel(plan_dir)
    if payload is None:
        print(
            f"chain-overnight: no sentinel present at {plan_dir}",
            file=sys.stderr,
        )
        return exit_codes.EXIT_OK  # idempotent — already released

    stamped_chain = payload.get("chain_id")
    if stamped_chain != args.chain_id:
        print(
            f"chain-overnight: refused release; sentinel chain_id="
            f"{stamped_chain!r} != requested {args.chain_id!r}",
            file=sys.stderr,
        )
        return exit_codes.EXIT_CHAIN_FOREIGN_SENTINEL

    driver_pid = payload.get("driver_pid")
    driver_host = payload.get("driver_host")
    if isinstance(driver_pid, int) and driver_host == socket.gethostname():
        if sentinel.is_pid_alive_locally(driver_pid):
            print(
                f"chain-overnight: refused release; driver_pid={driver_pid} "
                f"still alive on host {driver_host!r}",
                file=sys.stderr,
            )
            return exit_codes.EXIT_CHAIN_REFUSED

    # Log + remove.
    session_id = _driver_session_id(args.chain_id)
    state_machine.emit_release_lock(
        plan_dir,
        slug=args.slug,
        chain_id=args.chain_id,
        session_id=session_id,
        reason="operator invoked --release-lock; sentinel removed",
    )
    sentinel.release(plan_dir, chain_id=args.chain_id)

    # Per R-release-lock-pause (CCOR.1): --release-lock is the
    # operator's "force-clear all chain state" escape hatch. Also
    # remove the pause sentinel if present, and emit a
    # `chain_paused_lock_stale_cleared` log row when an actual removal
    # happened (silent no-op otherwise).
    try:
        removed_paused = pause_sentinel.release_if_present(plan_dir)
    except Exception:  # noqa: BLE001 — best-effort cleanup
        logger.warning(
            "pause sentinel release_if_present failed during --release-lock",
            exc_info=True,
        )
        removed_paused = False
    if removed_paused:
        _emit_paused_lock_stale_cleared(
            plan_dir, slug=args.slug, chain_id=args.chain_id,
        )

    print(f"chain-overnight: sentinel released for chain {args.chain_id}")
    return exit_codes.EXIT_OK


# ----------------------------------------------------------------------
# Chain run flow
# ----------------------------------------------------------------------


def _run_chain(args: argparse.Namespace) -> int:
    """Per implplan §A.impl + plan §A.5 full chain orchestration."""
    plan_dir = _resolve_plan_dir(args.slug)
    starting_phase: int = args.starting_phase
    if starting_phase not in PHASE_SEQUENCE:
        print(
            f"chain-overnight: starting_phase={starting_phase} not in "
            f"{PHASE_SEQUENCE}", file=sys.stderr,
        )
        return exit_codes.EXIT_DRIVER_CRASH

    # Resolve caps. CLI overrides → env → defaults.
    # Cost cap retired in `delete_usage_caps` (2026-05-23) — subscription
    # mode removes per-call budget enforcement.
    wall_clock_cap_seconds = (
        args.wall_clock_seconds
        if args.wall_clock_seconds is not None
        else cap_enforcement.resolve_wall_clock_cap()
    )

    # Resolve chain_id (resume or mint new).
    if args.from_resume:
        chain_id = args.from_resume
    else:
        chain_id = _mint_chain_id()

    # Per R-from-resume-symmetry / R-q14-symmetry (CCOR.1): on the
    # --from-resume recovery path, consume `_operator_inject.md` once
    # at startup BEFORE entering the phase loop. The framed inject
    # body is threaded into the first spawn after recovery via the
    # `_pending_inject_text` field on the finalizer so the per-phase
    # consume site (which competes with this startup consume for the
    # same file) sees it as the consumed body. If no inject file is
    # present, the helper returns None and recovery proceeds normally.
    _startup_inject_text: str | None = None
    if args.from_resume:
        _startup_inject_text = _consume_operator_inject_if_present(
            _resolve_plan_dir(args.slug),
            chain_id,
            slug=args.slug,
        )

    driver_session_id = _driver_session_id(chain_id)
    chain_started_epoch = _now_epoch()
    chain_started_at = _now_iso_z()
    driver_pid = os.getpid()
    driver_host = socket.gethostname()

    # Step 1 — Acquire sentinel (H.9 1C).
    acq = sentinel.acquire(
        plan_dir,
        chain_id=chain_id,
        driver_pid=driver_pid,
        driver_host=driver_host,
        wall_clock_cap_seconds=wall_clock_cap_seconds,
        started_at=chain_started_at,
    )
    if acq.status == "denied":
        print(
            f"chain-overnight: concurrent chain refused; live chain_id="
            f"{acq.live_chain_id!r}, sentinel={acq.sentinel_path}, "
            f"started_at={acq.live_started_at!r}",
            file=sys.stderr,
        )
        # Emit refused-transition row (best-effort; this is a driver-
        # initiated halt with no manifest yet, so we route through
        # state_machine.emit_chain_halted with halt_reason).
        try:
            state_machine.emit_chain_halted(
                plan_dir,
                slug=args.slug,
                chain_id=chain_id,
                session_id=driver_session_id,
                halt_reason="concurrent_chain_refused",
                detail=(
                    f"live_chain_id={acq.live_chain_id} "
                    f"sentinel={acq.sentinel_path}"
                ),
            )
        except Exception:  # noqa: BLE001
            pass
        return exit_codes.EXIT_CHAIN_REFUSED

    # Sentinel acquired. From here on, finally-block releases it.
    finalizer = _ChainFinalizer(
        plan_dir=plan_dir,
        slug=args.slug,
        chain_id=chain_id,
        driver_session_id=driver_session_id,
        chain_started_at=chain_started_at,
        chain_started_epoch=chain_started_epoch,
        wall_clock_cap_seconds=wall_clock_cap_seconds,
    )
    try:
        with _install_signal_handlers(finalizer):
            return _drive_phases(
                args=args,
                plan_dir=plan_dir,
                chain_id=chain_id,
                driver_session_id=driver_session_id,
                starting_phase=starting_phase,
                wall_clock_cap_seconds=wall_clock_cap_seconds,
                chain_started_at=chain_started_at,
                chain_started_epoch=chain_started_epoch,
                finalizer=finalizer,
                startup_inject_text=_startup_inject_text,
            )
    finally:
        # Always emit summary + release sentinel.
        finalizer.flush()


# ----------------------------------------------------------------------
# Phase loop
# ----------------------------------------------------------------------


def _drive_phases(
    *,
    args: argparse.Namespace,
    plan_dir: Path,
    chain_id: str,
    driver_session_id: str,
    starting_phase: int,
    wall_clock_cap_seconds: int,
    chain_started_at: str,
    chain_started_epoch: float,
    finalizer: "_ChainFinalizer",
    startup_inject_text: str | None = None,
) -> int:
    """Inner phase loop. Returns the final exit code.

    `startup_inject_text` carries the framed inject body that was
    consumed at startup on the `--from-resume` path (per
    R-from-resume-symmetry); it is threaded into the FIRST spawn after
    recovery, and then the per-phase consume helper takes over for
    subsequent phases.
    """
    slug = args.slug
    # Tracks whether the next-phase spawn should pick up the
    # startup-consumed inject (single-shot — cleared after first use).
    _pending_startup_inject: str | None = startup_inject_text

    # Step 2 — Stamp manifest.
    manifest = manifest_mod.stamp_chain_start(
        plan_dir,
        slug=slug,
        chain_id=chain_id,
        chain_started_at=chain_started_at,
        wall_clock_cap_seconds=wall_clock_cap_seconds,
    )

    # Step 3 — Auto-register (§A.impl.5b; best-effort; §P not built).
    ar_result = auto_register.auto_register_chain_session(
        chain_id=chain_id,
        slug=slug,
        manifest=manifest,
        mode_flags=("overnight",),
    )
    if ar_result.status == "registered" or ar_result.status == "failed_open":
        try:
            state_machine.emit_chain_session_auto_registered(
                plan_dir,
                slug=slug,
                chain_id=chain_id,
                session_id=driver_session_id,
                intent_session_id=ar_result.session_id,
            )
        except Exception:  # noqa: BLE001 — best-effort
            logger.warning("emit_chain_session_auto_registered failed", exc_info=True)
        # Propagate the intent session id into the process env so every
        # phase subprocess (and its §P PreToolUse hook) can resolve the
        # current session via the fast-path env-var lookup per §P.impl.9
        # step 2(a) instead of falling through to the per-host JSONL scan.
        if ar_result.session_id:
            os.environ["SPLOCK_INTENT_SESSION_ID"] = ar_result.session_id
    # Status `opt_out` → no log row (settings opt-out is a silent no-op).
    # Status `collision_detected` → caller should halt per §P.impl.6; with
    # §P not built, we treat this status as failed_open (chain proceeds).

    # Step 4 — Emit chain-start transition.
    state_machine.emit_chain_start(
        plan_dir,
        slug=slug,
        chain_id=chain_id,
        session_id=driver_session_id,
        reason=f"chain start; phase={starting_phase}",
    )

    # Step 5 — Loop phases.
    for phase in PHASE_SEQUENCE:
        if phase < starting_phase:
            continue  # skipped via --from-resume

        # Pause-probe (CCOR.1 R-granularity + R-poll-interval +
        # R-needs-human-precedence + R-pause-probe-needs-human-break).
        # Phase-boundary only — the probe sits between the end of phase
        # N's commit and the cap-check for phase N+1. If pause is
        # requested while phase N is mid-iteration, the iteration loop
        # finishes its current iteration entirely, control returns to
        # this for-loop top, and we park here until the operator's
        # `bin/chain-resume` removes the pause sentinel.
        #
        # NEEDS_HUMAN halt takes precedence over pause: if a halt fires
        # while paused (via halt-handoff from a previous phase or a
        # foreign-sentinel check), the probe breaks out and the existing
        # halt unwind takes over (the finalizer's pause-sentinel cleanup
        # in flush() removes the sentinel as part of normal cleanup).
        #
        # Python's `time.sleep()` honors signals, so SIGTERM during pause
        # unwinds cleanly via the existing `_install_signal_handlers`
        # KeyboardInterrupt-style mechanism — no extra signal handling
        # needed at this layer.
        while pause_sentinel.is_held(plan_dir):
            if finalizer.halt_reason == "needs_human":
                break
            time.sleep(POLL_INTERVAL_SECONDS)

        finalizer.note_phase_entry(phase)

        # Pre-spawn wall-clock cap check (cost-cap arm retired in
        # `delete_usage_caps`).
        cap_verdict = phase_spawn.precheck_caps(
            plan_dir,
            chain_id=chain_id,
            phase=phase,
            chain_started_at_epoch=chain_started_epoch,
        )
        if cap_verdict.verdict == "wall_clock_exceeded":
            state_machine.emit_phase_boundary(
                plan_dir,
                slug=slug,
                chain_id=chain_id,
                session_id=driver_session_id,
                phase=phase,
                verdict="wall_clock_exceeded",
                reason=(
                    f"remaining={cap_verdict.seconds_remaining:.0f}s "
                    f"< estimate={cap_verdict.estimated_phase_seconds:.0f}s"
                ),
            )
            finalizer.set_halt(
                halt_reason="wall_clock_exceeded",
                exit_code=exit_codes.EXIT_WALL_CLOCK_CAP,
            )
            return exit_codes.EXIT_WALL_CLOCK_CAP

        # Foreign-sentinel re-check (per A.impl.4).
        if not phase_spawn.recheck_foreign_sentinel(plan_dir, chain_id):
            state_machine.emit_phase_boundary(
                plan_dir,
                slug=slug,
                chain_id=chain_id,
                session_id=driver_session_id,
                phase=phase,
                verdict="concurrent_chain_refused",
                reason="foreign sentinel detected at STARTING→RUNNING edge",
            )
            finalizer.set_halt(
                halt_reason="concurrent_chain_foreign",
                exit_code=exit_codes.EXIT_CHAIN_FOREIGN_SENTINEL,
            )
            return exit_codes.EXIT_CHAIN_FOREIGN_SENTINEL

        # Append phase bootstrap row to manifest (mirrors SessionStart hook).
        # This is a driver-driven append since §G's hook is not yet wired
        # to fire; once §G ships, the SessionStart hook is the canonical
        # writer. For now, the driver duplicates the writer role.
        session_id_phase = phase_spawn._resolve_session_id(chain_id, phase)
        try:
            manifest_mod.append_phase_bootstrap(
                plan_dir,
                phase=phase,
                session_id=session_id_phase,
                slug=slug,
                chain_id=chain_id,
                source="chain",
            )
        except Exception:  # noqa: BLE001 — best-effort bootstrap
            logger.warning("manifest append_phase_bootstrap failed", exc_info=True)

        # Consume operator-inject (single-shot) BEFORE the spawn so the
        # planner / opus-iter prompt sees the framed inject text. Per
        # R-inject-wiring: planner spawn (phases 2, 3) AND retry-loop
        # spawn (phases 4, 5) consume; the reviewer spawn inside
        # run_test_step_loop never receives the inject (verdict-only
        # determinism per userguide §3.6). Phase 4/5 also performs a
        # per-iteration consume INSIDE `run_test_step_loop` (handled in
        # `bin/_retry_loop/iteration_loop.py`); both call sites compete
        # for the same file and first-one-wins is fine because of the
        # single-shot delete contract.
        #
        # On the --from-resume path, the operator's inject may already
        # have been consumed at startup; in that case
        # `_pending_startup_inject` carries the body and is used here
        # (single-shot — cleared after the first spawn).
        inject_text = _consume_operator_inject_if_present(
            plan_dir, chain_id, slug=slug,
        )
        if inject_text is None and _pending_startup_inject is not None:
            inject_text = _pending_startup_inject
        _pending_startup_inject = None

        # Spawn the phase.
        if phase in (2, 3):
            phase_result = phase_spawn.spawn_planner_phase(
                plan_dir,
                slug=slug,
                chain_id=chain_id,
                phase=phase,
                inject_text=inject_text,
            )
        else:
            phase_result = phase_spawn.spawn_retry_loop_phase(
                plan_dir,
                slug=slug,
                chain_id=chain_id,
                phase=phase,
                inject_text=inject_text,
            )

        # Stamp manifest exit-time fields.
        try:
            manifest_mod.stamp_phase_exit(
                plan_dir,
                session_id=phase_result.session_id,
                ended_at=phase_result.ended_at,
                exit_code=phase_result.exit_code,
                cost_usd=phase_result.cost_usd,
                model_id=phase_result.model_id,
            )
        except Exception:  # noqa: BLE001
            logger.warning("manifest stamp_phase_exit failed", exc_info=True)

        finalizer.note_phase_result(phase_result)

        # On halt verdict → emit + return.
        if phase_result.verdict != "passed":
            state_machine.emit_phase_boundary(
                plan_dir,
                slug=slug,
                chain_id=chain_id,
                session_id=phase_result.session_id,
                phase=phase,
                verdict=phase_result.verdict,
                reason=(phase_result.halt_reason or phase_result.verdict),
                exit_code=phase_result.exit_code,
            )
            finalizer.set_halt(
                halt_reason=phase_result.verdict,
                exit_code=phase_result.exit_code,
            )
            return phase_result.exit_code

        # DEFERRED INTEGRATION (Phase 2 post-phase review M-1):
        # The §F.9 phase-boundary review gate machinery
        # (`bin._retry_loop.phase_boundary_review.run_boundary_review`)
        # is built and tested in isolation (136 §F tests pass) but is
        # NOT yet wired into this phase loop. The intended call site
        # is HERE — after a phase passes, before advancing to the next
        # phase — with `boundary="plan_to_implplan"` (between phase 2/3)
        # or `boundary="implplan_to_code"` (between phase 3/4). The
        # unified counter (anchor §4a.3 element 1) must be threaded
        # through correctly. Deferred to a focused Phase 3 follow-up
        # marker to land the integration with proper test coverage.

        # Pre-stage safety net (Finding 2) + git commit (subprocess).
        scan = pre_stage.scan_for_git_operation(_REPO_ROOT)
        if scan.verdict == "refuse":
            first = scan.first_match
            if first is not None:
                matched_path, matched_pattern = first
            else:
                matched_path, matched_pattern = "<unknown>", "<unknown>"
            state_machine.emit_sealed_path_refused(
                plan_dir,
                slug=slug,
                chain_id=chain_id,
                session_id=phase_result.session_id,
                matched_path=matched_path,
                matched_pattern=matched_pattern,
            )
            finalizer.set_halt(
                halt_reason="sealed_path_refused",
                exit_code=exit_codes.EXIT_SEALED_PATH_REFUSED,
            )
            return exit_codes.EXIT_SEALED_PATH_REFUSED

        # Phase passed; emit per-phase boundary row.
        state_machine.emit_phase_boundary(
            plan_dir,
            slug=slug,
            chain_id=chain_id,
            session_id=phase_result.session_id,
            phase=phase,
            verdict="passed",
            reason=f"phase {phase} completed",
            exit_code=phase_result.exit_code,
        )

    # All phases complete — terminal success.
    state_machine.emit_chain_complete(
        plan_dir,
        slug=slug,
        chain_id=chain_id,
        session_id=driver_session_id,
        reason=f"all phases {PHASE_SEQUENCE} completed",
    )
    finalizer.set_halt(
        halt_reason="phase_success",
        exit_code=exit_codes.EXIT_OK,
    )
    return exit_codes.EXIT_OK


# ----------------------------------------------------------------------
# Finalizer: emits completion summary + releases sentinel
# ----------------------------------------------------------------------


class _ChainFinalizer:
    """Captures per-phase trajectory + emits the path-1 completion summary.

    Lives for the duration of a chain run. `flush()` is called from the
    outer finally block in `_run_chain` — guarantees emit-summary + release-
    sentinel regardless of how the chain exits (normal, halt, exception).
    """

    def __init__(
        self,
        *,
        plan_dir: Path,
        slug: str,
        chain_id: str,
        driver_session_id: str,
        chain_started_at: str,
        chain_started_epoch: float,
        wall_clock_cap_seconds: int,
    ) -> None:
        self.plan_dir = plan_dir
        self.slug = slug
        self.chain_id = chain_id
        self.driver_session_id = driver_session_id
        self.chain_started_at = chain_started_at
        self.chain_started_epoch = chain_started_epoch
        self.wall_clock_cap_seconds = wall_clock_cap_seconds
        self.phase_results: list[phase_spawn.PhaseResult] = []
        self.halt_reason: str = "phase_success"
        self.exit_code: int = exit_codes.EXIT_OK
        self._flushed = False

    def note_phase_entry(self, phase: int) -> None:
        """Hook for future per-phase logging (currently a no-op)."""

    def note_phase_result(self, result: phase_spawn.PhaseResult) -> None:
        self.phase_results.append(result)

    def set_halt(self, *, halt_reason: str, exit_code: int) -> None:
        self.halt_reason = halt_reason
        self.exit_code = exit_code

    def flush(self) -> None:
        """Emit summary + release sentinel(s). Idempotent.

        Per R-finalizer-cleanup (CCOR.1): the pause sentinel
        (`_chain_paused.lock`) is released on EVERY chain-end path
        (success, halt, SIGTERM/KeyboardInterrupt, exception unwind,
        needs_human). `release_if_present` is no-raise and returns True
        iff the sentinel was actually present; the
        `chain_paused_lock_stale_cleared` log row is emitted ONLY on
        True (so successful clean-exits don't spam the log).
        """
        if self._flushed:
            return
        self._flushed = True
        try:
            self._emit_summary()
        except Exception:  # noqa: BLE001 — emit failure is non-fatal
            logger.warning("completion summary emit failed", exc_info=True)
        # Pause-sentinel cleanup runs BEFORE the H.9 1C release so a
        # failure to release the running lock (which is mapped to a
        # warning, not raised) doesn't skip the pause-sentinel cleanup.
        try:
            removed = pause_sentinel.release_if_present(self.plan_dir)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            logger.warning(
                "pause sentinel release_if_present failed", exc_info=True,
            )
            removed = False
        if removed:
            _emit_paused_lock_stale_cleared(
                self.plan_dir,
                slug=self.slug,
                chain_id=self.chain_id,
            )
        try:
            sentinel.release(self.plan_dir, chain_id=self.chain_id)
        except sentinel.SentinelChainIdMismatch:
            logger.warning("sentinel chain_id mismatch on release")
        except Exception:  # noqa: BLE001 — release failure is non-fatal
            logger.warning("sentinel release failed", exc_info=True)

    def _emit_summary(self) -> None:
        # Build the completion-summary payload from accumulated state.
        ended_at = _now_iso_z()
        ended_epoch = _now_epoch()
        total_cost = sum(r.cost_usd for r in self.phase_results)
        phases = tuple(
            completion_summary.ChainPhaseRecord(
                phase=r.phase,
                phase_command=phase_spawn.PHASE_COMMAND_MAP.get(r.phase, "?"),
                started_at=r.started_at,
                ended_at=r.ended_at,
                exit_code=r.exit_code,
                cost_usd=r.cost_usd,
                result=r.verdict,
            )
            for r in self.phase_results
        )
        payload = completion_summary.CompletionSummaryInput(
            slug=self.slug,
            chain_id=self.chain_id,
            chain_started_at=self.chain_started_at,
            chain_ended_at=ended_at,
            halt_reason=self.halt_reason,
            driver_exit_code=self.exit_code,
            phases=phases,
            committed_files=(),  # populated by future driver post-commit phase
            morning_review_pointers=(),
            cost_total_usd=total_cost,
            wall_clock_cap_seconds=self.wall_clock_cap_seconds,
            wall_clock_total_seconds=int(ended_epoch - self.chain_started_epoch),
            mode_flags={"overnight": True},
        )
        # PER ORCHESTRATOR §4a.4 ANCHOR:
        # emit_chain_summary's contract is "the summary write is LAST".
        # Anything downstream of this call that may fail leaves the
        # summary durable.
        completion_summary.emit_chain_summary(self.plan_dir, payload)


# ----------------------------------------------------------------------
# Signal handlers — operator kill (per A.impl.10 #2 RATIFIED)
# ----------------------------------------------------------------------


@contextmanager
def _install_signal_handlers(finalizer: "_ChainFinalizer") -> Iterator[None]:
    """SIGTERM handler — set halt + raise to unwind to finally block.

    Per A.impl.10 #2 RATIFIED 2026-05-21: operator-kill via SIGTERM
    triggers the Stop hook verification-artifact emit (in spawned step
    agents); the driver itself receives SIGTERM, sets the halt reason,
    and unwinds via _ChainFinalizer.flush() in the outer finally.
    """
    def _on_sigterm(signum: int, frame: Any) -> None:
        finalizer.set_halt(
            halt_reason="operator_killed",
            exit_code=exit_codes.EXIT_OPERATOR_KILLED,
        )
        logger.warning("SIGTERM received; halting")
        raise KeyboardInterrupt("SIGTERM")

    prior_term = signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, prior_term)


if __name__ == "__main__":
    sys.exit(main())
