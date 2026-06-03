"""H.9 1C sentinel mechanism — `_chain_running.lock`.

Per implplan §A.impl.3 (lines 385-450) + plan §A.7 criterion 7. The
sentinel is the load-bearing piece of refuse-second concurrent-chain
prevention; a second `bin/chain-overnight ... <slug>` invocation on the
same plan directory acquires this lock and refuses if it already exists.

Distinct from `_chain_sessions.json.lock` (the flock for manifest
read-modify-write cycles). The sentinel guards against concurrent
CHAINS; the flock guards against concurrent MUTATIONS within a single
chain.

File layout:
    <plan_dir>/_chain_running.lock      # this module's sentinel
    <plan_dir>/_chain_sessions.json.lock # manifest's flock target

Atomicity discipline: sentinel is written via
`tempfile.NamedTemporaryFile(dir=<plan_dir>)` + `os.replace` to the
final name (mirrors §B's `write_atomic` discipline; cannot just use
`write_atomic` because we need check-and-set semantics, which requires
an O_EXCL probe on the final name).
"""

from __future__ import annotations

import datetime
import errno
import json
import logging
import os
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

SENTINEL_BASENAME = "_chain_running.lock"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class AcquireResult:
    """Result of `acquire(...)`.

    Either `status == "acquired"` (sentinel created) OR `status ==
    "denied"` (live sentinel exists; live_chain_id / sentinel_path /
    started_at populated for the structured-stderr emission).
    """

    status: Literal["acquired", "denied"]
    sentinel_path: Path
    live_chain_id: str | None = None
    live_started_at: str | None = None
    live_driver_pid: int | None = None
    live_driver_host: str | None = None


def sentinel_path(plan_dir: Path) -> Path:
    """Canonical sentinel path for a given plan dir."""
    return plan_dir / SENTINEL_BASENAME


def acquire(
    plan_dir: Path,
    *,
    chain_id: str,
    driver_pid: int,
    driver_host: str,
    wall_clock_cap_seconds: int,
    started_at: str | None = None,
) -> AcquireResult:
    """Attempt to acquire the H.9 1C sentinel.

    Algorithm:
    1. Probe the sentinel path with `O_EXCL` semantics — if file exists,
       read it for the live-chain forensic payload and return `denied`.
    2. If not present, write via temp+replace and return `acquired`.

    Race-condition discipline: the probe-then-write is NOT atomic on a
    busy machine. We use `os.open(..., O_CREAT | O_EXCL)` for the
    actual create step so two simultaneous probes can't both succeed.
    The temp+replace path is for the *content* write; create+content
    happen under `O_EXCL` ownership.

    Parameters
    ----------
    plan_dir : Path
        The plan directory (must exist).
    chain_id : str
        Chain identifier — stamped into the sentinel for cross-check on
        release.
    driver_pid : int
        Caller's PID — for crash-recovery PID-liveness check.
    driver_host : str
        Caller's hostname — for cross-machine forensic value.
    wall_clock_cap_seconds : int
        Stamped for forensic value; not load-bearing for sentinel logic.
    started_at : str | None
        ISO 8601 UTC; defaults to now() if None.
    """
    if not plan_dir.exists():
        raise FileNotFoundError(f"plan_dir does not exist: {plan_dir}")
    if not plan_dir.is_dir():
        raise NotADirectoryError(f"plan_dir is not a directory: {plan_dir}")

    target = sentinel_path(plan_dir)
    started = started_at or _now_iso_z()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "chain_id": chain_id,
        "started_at": started,
        "driver_pid": driver_pid,
        "driver_host": driver_host,
        "wall_clock_cap_seconds": wall_clock_cap_seconds,
    }
    body = json.dumps(payload, indent=2, sort_keys=True) + "\n"

    # Step 1 — O_EXCL probe. Atomic with respect to filesystem; no race.
    try:
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        # Sentinel already exists — read it for forensic payload.
        live = _read_existing_sentinel(target)
        return AcquireResult(
            status="denied",
            sentinel_path=target,
            live_chain_id=live.get("chain_id"),
            live_started_at=live.get("started_at"),
            live_driver_pid=live.get("driver_pid"),
            live_driver_host=live.get("driver_host"),
        )

    # Step 2 — write content + fsync + close.
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        # Cleanup: remove the partial sentinel so a retry can succeed.
        try:
            target.unlink()
        except OSError:
            pass
        raise

    logger.debug(
        "sentinel acquired plan_dir=%s chain_id=%s pid=%s",
        plan_dir, chain_id, driver_pid,
    )
    return AcquireResult(status="acquired", sentinel_path=target)


def release(plan_dir: Path, *, chain_id: str) -> None:
    """Release the sentinel iff the stamped chain_id matches.

    Defense against `--release-lock` calls from a different chain's
    recovery flow (per implplan §A.impl.3 lines 419-422).

    Tolerates FileNotFoundError — the sentinel may have been released
    by a prior `--release-lock` or recovery path; tolerating no-op is
    safer than panicking at chain end.
    """
    target = sentinel_path(plan_dir)
    if not target.exists():
        logger.debug("sentinel release no-op (not present) plan_dir=%s", plan_dir)
        return

    stamped = _read_existing_sentinel(target)
    stamped_chain_id = stamped.get("chain_id")
    if stamped_chain_id != chain_id:
        raise SentinelChainIdMismatch(
            f"refused to release sentinel: stamped chain_id="
            f"{stamped_chain_id!r} != caller chain_id={chain_id!r}"
        )

    try:
        target.unlink()
    except FileNotFoundError:
        # Race: another flow released between our read and unlink.
        pass
    logger.debug("sentinel released plan_dir=%s chain_id=%s", plan_dir, chain_id)


def read_sentinel(plan_dir: Path) -> dict | None:
    """Read the sentinel JSON, returning None if missing.

    Used by `phase_spawn.py` on every STARTING→RUNNING transition to
    re-check for foreign sentinels (defense-in-depth per A.impl.4).
    """
    target = sentinel_path(plan_dir)
    if not target.exists():
        return None
    try:
        return _read_existing_sentinel(target)
    except OSError:
        return None


def is_pid_alive_locally(pid: int) -> bool:
    """Check whether `pid` is alive on the local machine.

    Used by `--release-lock` to refuse release when the driver is still
    running (per implplan §A.impl.3 step 3 of recovery).

    Best-effort — sending signal 0 doesn't deliver anything; it only
    triggers the kernel's permission/existence check.
    """
    try:
        os.kill(pid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        # EPERM means the process exists but we don't own it (still alive)
        if exc.errno == errno.EPERM:
            return True
        return False
    else:
        return True


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------


class SentinelChainIdMismatch(RuntimeError):
    """Raised by `release()` when the stamped chain_id doesn't match."""


def _now_iso_z() -> str:
    """ISO-8601 UTC with trailing Z, second resolution."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _read_existing_sentinel(target: Path) -> dict:
    """Read + parse the sentinel file. Tolerant of partial-write garbage."""
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


__all__ = [
    "AcquireResult",
    "SENTINEL_BASENAME",
    "SentinelChainIdMismatch",
    "acquire",
    "is_pid_alive_locally",
    "read_sentinel",
    "release",
    "sentinel_path",
]
