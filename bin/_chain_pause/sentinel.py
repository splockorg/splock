"""Pause sentinel — `_chain_paused.lock` primitives (CCOR.1 T-4).

The pause sentinel parks a live chain at the next phase boundary. It is
distinct from the H.9 1C running sentinel (`_chain_running.lock`,
implemented in `bin/_chain_overnight/sentinel.py`) — the running sentinel
guards against concurrent **chains**; this pause sentinel signals the
**driver of the live chain** to halt at the next phase boundary and
honors operator-side pause/resume requests.

Per the design resolutions in `docs/plans/_closed/ccor_1/design_resolutions.md`:

- **R-sentinel-primitive.** Atomic acquire via
  `os.open(..., O_CREAT | O_EXCL | O_WRONLY)` (mirrors
  `bin/_chain_overnight/sentinel.py::acquire` line ~126). The choice
  matters because the failure mode we're defending against is
  double-create (two concurrent `bin/chain-pause` invocations racing
  on the same plan dir); a temp+rename pattern would be atomic
  against torn writes but would NOT refuse a second create.
  `acquire` raises `PauseAlreadyHeldError` on `EEXIST`; the CLI
  (T-5) translates that to `EXIT_ALREADY_PAUSED = 23` (non-idempotent
  — the second pause is an informative refusal, not silent noop).

- **R-sentinel-fields.** Payload shape (schema_version = 1):
  ``{schema_version: 1, chain_id: str, paused_at: ISO-8601,
  paused_by_pid: int, paused_by_host: str, reason: str | None,
  next_phase_to_enter: int}``. `next_phase_to_enter` is computed at
  pause-request time from the manifest and may go stale by one phase
  if the driver completes another phase between the read and the
  pause-probe at the next phase boundary — this is acceptable for
  status display and not load-bearing for correctness. The freshness
  caveat is documented in the userguide (T-10) and in
  `bin/chain-status`'s help text (T-8).

Schema-version bump rule (T-10 docstring, per R-sentinel-fields):

- **Bump `PAUSE_SENTINEL_SCHEMA_VERSION` on field-shape change.**
  Adding, removing, or renaming a field in the payload requires a
  schema-version bump, so consumers (`bin/chain-resume`,
  `bin/chain-status`, the driver's pause-probe) can branch on the
  version and read v1-shaped sentinels without crashing.
- **Never bump on field-value semantic change.** A new valid value
  for `reason` (e.g., expanding the operator-supplied free-text to
  accept a structured enum later) does NOT require a schema bump —
  the field is still a string. Same goes for `next_phase_to_enter`
  growing past phase 5 if the driver later adds phases.

This mirrors the existing `bin/_chain_overnight/manifest.py`
`SCHEMA_VERSION` discipline: shape-change ⇒ bump, value-change ⇒
do not bump.

Primitives (used by T-5 chain-pause, T-6 chain-resume, T-7 driver):

- `acquire(plan_dir, chain_id, paused_at, paused_by_pid,
  paused_by_host, reason, next_phase_to_enter)` — atomic O_EXCL
  create. Raises `PauseAlreadyHeldError` if the sentinel already
  exists.
- `release(plan_dir, chain_id)` — reads the sentinel, verifies the
  on-disk `chain_id` matches the caller's, raises
  `PauseChainIdMismatchError` (without removing) on mismatch.
- `release_if_present(plan_dir)` — best-effort cleanup used by the
  finalizer (T-7). Returns `True` if the sentinel was actually
  present and removed; `False` if absent. Swallows
  `FileNotFoundError`.
- `read(plan_dir)` — returns the parsed payload dict or `None` if
  the sentinel is absent. Raises `PauseSentinelCorruptError` on
  malformed JSON (distinct from "missing" — a present-but-corrupt
  sentinel is a state the operator must reconcile).
- `is_held(plan_dir)` — boolean convenience over `read`; the driver's
  pause-probe (T-7) uses this in its sleep loop.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


PAUSE_SENTINEL_FILENAME = "_chain_paused.lock"
PAUSE_SENTINEL_SCHEMA_VERSION = 1


# ----------------------------------------------------------------------
# Exception family
# ----------------------------------------------------------------------


class PauseAlreadyHeldError(RuntimeError):
    """Raised by `acquire()` when the pause sentinel already exists.

    Maps to `EXIT_ALREADY_PAUSED = 23` at the CLI layer (T-5). The error
    carries the existing sentinel's `chain_id` (where readable) so the
    CLI can render a useful diagnostic.
    """

    def __init__(self, sentinel_path: Path, live_chain_id: str | None = None) -> None:
        self.sentinel_path = sentinel_path
        self.live_chain_id = live_chain_id
        super().__init__(
            f"pause sentinel already held: {sentinel_path} "
            f"(live chain_id={live_chain_id!r})"
        )


class PauseChainIdMismatchError(RuntimeError):
    """Raised by `release()` when the on-disk chain_id doesn't match.

    The sentinel is NOT removed on mismatch — preserves forensic state
    for the operator. The CLI (T-6) maps this to `EXIT_DRIVER_CRASH = 2`
    with the "chain_id mismatch" diagnostic.
    """

    def __init__(self, expected: str, on_disk: str | None) -> None:
        self.expected = expected
        self.on_disk = on_disk
        super().__init__(
            f"pause sentinel chain_id mismatch: caller expected "
            f"{expected!r}, on-disk {on_disk!r}"
        )


class PauseSentinelCorruptError(RuntimeError):
    """Raised by `read()` when the sentinel exists but is malformed.

    Distinct from "missing" — a corrupt sentinel is a state the operator
    must reconcile. The CLI exits with diagnostic; the driver's
    pause-probe treats this as "not paused" defensively (better to keep
    driving than to halt on garbage).
    """

    def __init__(self, sentinel_path: Path, reason: str) -> None:
        self.sentinel_path = sentinel_path
        self.reason = reason
        super().__init__(f"pause sentinel corrupt at {sentinel_path}: {reason}")


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------


def _sentinel_path(plan_dir: Path) -> Path:
    """Canonical sentinel path for a given plan dir."""
    return plan_dir / PAUSE_SENTINEL_FILENAME


def _now_iso_z() -> str:
    """ISO-8601 UTC with trailing Z, second resolution.

    Mirrors `bin/_chain_overnight/sentinel.py::_now_iso_z` so timestamp
    formats are consistent across the chain-running and chain-paused
    sentinels.
    """
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _parse_existing_payload(target: Path) -> dict:
    """Read + parse the sentinel file strictly.

    Raises `PauseSentinelCorruptError` on:
    - empty file,
    - non-JSON content,
    - JSON that does not decode to a dict.

    Returns the parsed dict on success. Used by `read()`, `release()`,
    and (indirectly) `acquire()`'s denial-path forensic readback.
    """
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise PauseSentinelCorruptError(target, f"OSError: {exc}") from exc
    if not text.strip():
        raise PauseSentinelCorruptError(target, "empty file")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PauseSentinelCorruptError(target, f"JSONDecodeError: {exc}") from exc
    if not isinstance(parsed, dict):
        raise PauseSentinelCorruptError(
            target, f"top-level not an object (got {type(parsed).__name__})"
        )
    return parsed


# ----------------------------------------------------------------------
# Public primitives
# ----------------------------------------------------------------------


def acquire(
    plan_dir: Path,
    *,
    chain_id: str,
    paused_at: str | None = None,
    paused_by_pid: int,
    paused_by_host: str,
    reason: str | None = None,
    next_phase_to_enter: int,
) -> dict:
    """Atomically acquire the pause sentinel; refuse if already held.

    Per R-sentinel-primitive: uses `os.open(..., O_CREAT | O_EXCL |
    O_WRONLY)` so two concurrent callers cannot both succeed. The
    create-then-write happens under O_EXCL ownership (the temp+rename
    pattern is NOT used here because we need check-and-refuse semantics
    on a second create).

    Parameters
    ----------
    plan_dir : Path
        The plan directory (must exist; raises FileNotFoundError
        otherwise).
    chain_id : str
        Chain identifier — stamped into the sentinel for cross-check on
        release. Must match the on-disk `_chain_running.lock`'s
        chain_id (the CLI layer enforces this in T-5).
    paused_at : str | None
        ISO-8601 UTC; defaults to now() if None.
    paused_by_pid : int
        Operator-CLI PID (NOT the driver PID — useful for forensic
        cross-reference with `bin/chain-pause`'s shell history).
    paused_by_host : str
        Operator-CLI hostname.
    reason : str | None
        Operator-supplied free-text reason; defaults to None.
    next_phase_to_enter : int
        Phase the driver will enter when the pause-probe next runs;
        computed by `bin/chain-pause` from the manifest at write time.

    Returns
    -------
    dict
        The payload that was written (also available via `read()`).

    Raises
    ------
    PauseAlreadyHeldError
        The sentinel already exists. The CLI maps this to
        `EXIT_ALREADY_PAUSED = 23`.
    FileNotFoundError
        `plan_dir` does not exist.
    NotADirectoryError
        `plan_dir` is not a directory.
    """
    if not plan_dir.exists():
        raise FileNotFoundError(f"plan_dir does not exist: {plan_dir}")
    if not plan_dir.is_dir():
        raise NotADirectoryError(f"plan_dir is not a directory: {plan_dir}")

    target = _sentinel_path(plan_dir)
    stamped_at = paused_at or _now_iso_z()
    payload = {
        "schema_version": PAUSE_SENTINEL_SCHEMA_VERSION,
        "chain_id": chain_id,
        "paused_at": stamped_at,
        "paused_by_pid": paused_by_pid,
        "paused_by_host": paused_by_host,
        "reason": reason,
        "next_phase_to_enter": next_phase_to_enter,
    }
    body = json.dumps(payload, indent=2, sort_keys=True) + "\n"

    # Step 1 — O_EXCL probe. Atomic at the filesystem layer; no race.
    try:
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        # Sentinel already exists — best-effort read for the diagnostic
        # so the caller can include the live chain_id in its error
        # message. We swallow read errors here (the file is present but
        # may be partially written; the refusal itself is the contract,
        # not the diagnostic).
        live_chain_id: str | None = None
        try:
            existing = _parse_existing_payload(target)
            live_chain_id = existing.get("chain_id")
        except PauseSentinelCorruptError:
            live_chain_id = None
        raise PauseAlreadyHeldError(target, live_chain_id=live_chain_id)

    # Step 2 — write content + fsync + close. Cleanup on failure so a
    # retry can succeed (mirrors the chain-overnight sentinel discipline).
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        try:
            target.unlink()
        except OSError:
            pass
        raise

    logger.debug(
        "pause sentinel acquired plan_dir=%s chain_id=%s next_phase=%s",
        plan_dir, chain_id, next_phase_to_enter,
    )
    return payload


def release(plan_dir: Path, *, chain_id: str) -> None:
    """Release the pause sentinel iff the on-disk chain_id matches.

    Per R-orphan-detection: chain-id mismatch raises
    `PauseChainIdMismatchError` WITHOUT removing the sentinel —
    preserves forensic state for the operator (the mismatch implies a
    cross-chain release attempt or a stale sentinel from a prior chain
    that should be inspected, not silently cleaned).

    Parameters
    ----------
    plan_dir : Path
        The plan directory.
    chain_id : str
        Expected chain_id. Must match the on-disk sentinel's chain_id
        for the release to proceed.

    Raises
    ------
    FileNotFoundError
        The sentinel is absent. Callers that want best-effort cleanup
        on the success/halt paths should use `release_if_present`
        instead.
    PauseChainIdMismatchError
        The on-disk chain_id does not match the caller's. The sentinel
        is left intact.
    PauseSentinelCorruptError
        The sentinel exists but is malformed; the operator must hand-
        resolve. The sentinel is left intact.
    """
    target = _sentinel_path(plan_dir)
    if not target.exists():
        raise FileNotFoundError(
            f"pause sentinel absent at {target}; use release_if_present "
            f"for best-effort cleanup"
        )

    payload = _parse_existing_payload(target)
    on_disk_chain_id = payload.get("chain_id")
    if on_disk_chain_id != chain_id:
        raise PauseChainIdMismatchError(expected=chain_id, on_disk=on_disk_chain_id)

    try:
        target.unlink()
    except FileNotFoundError:
        # Race: another flow released between our read and unlink.
        # Treat as success — the desired post-condition (sentinel gone)
        # is met.
        pass
    logger.debug("pause sentinel released plan_dir=%s chain_id=%s", plan_dir, chain_id)


def release_if_present(plan_dir: Path) -> bool:
    """Best-effort sentinel removal; no chain_id check.

    Used by `_ChainFinalizer.flush()` (T-7) on every chain-end path
    (success verdict, halt verdict, SIGTERM unwind, KeyboardInterrupt,
    exception unwind) and by `bin/chain-overnight --release-lock` (T-7)
    as the operator's "force-clear" escape hatch.

    Returns
    -------
    bool
        `True` if the sentinel was actually present and removed;
        `False` if absent. The finalizer emits a
        `chain_paused_lock_stale_cleared` log row only on a return of
        `True` (so successful clean-exits don't spam the log — per
        R-finalizer-cleanup).
    """
    target = _sentinel_path(plan_dir)
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        # Some other OS error (permissions, etc.) — surface it so the
        # operator can investigate. The finalizer can decide whether to
        # swallow or propagate.
        logger.warning(
            "pause sentinel removal failed plan_dir=%s: %s",
            plan_dir, exc,
        )
        raise
    logger.debug("pause sentinel released (best-effort) plan_dir=%s", plan_dir)
    return True


def read(plan_dir: Path) -> dict | None:
    """Read the sentinel payload; return None if absent.

    Used by:
    - `bin/chain-resume` (T-6) to verify pause state + chain_id before
      releasing.
    - `bin/chain-status` (T-8) to render PAUSED state in operator
      output.
    - The driver's pause-probe (T-7) via `is_held` (which is a thin
      wrapper).

    Raises
    ------
    PauseSentinelCorruptError
        The sentinel is present but malformed (empty, not JSON, or not
        a JSON object). Distinct from "absent" — callers must decide
        whether to halt or proceed defensively.
    """
    target = _sentinel_path(plan_dir)
    if not target.exists():
        return None
    return _parse_existing_payload(target)


def is_held(plan_dir: Path) -> bool:
    """Convenience boolean over `read()`.

    Used by the driver's pause-probe (T-7) in its sleep loop:

        while pause_sentinel.is_held(plan_dir):
            ...
            time.sleep(POLL_INTERVAL_SECONDS)

    Defensive on corrupt payloads: a malformed sentinel is treated as
    "not held" so the driver keeps making progress rather than halting
    on garbage. The corruption surfaces via `read()` when the operator
    runs `bin/chain-resume` or `bin/chain-status`.
    """
    target = _sentinel_path(plan_dir)
    if not target.exists():
        return False
    try:
        _parse_existing_payload(target)
    except PauseSentinelCorruptError:
        logger.warning(
            "pause sentinel present but corrupt at %s; treating as not-held",
            target,
        )
        return False
    return True


__all__ = [
    "PAUSE_SENTINEL_FILENAME",
    "PAUSE_SENTINEL_SCHEMA_VERSION",
    "PauseAlreadyHeldError",
    "PauseChainIdMismatchError",
    "PauseSentinelCorruptError",
    "acquire",
    "is_held",
    "read",
    "release",
    "release_if_present",
]
