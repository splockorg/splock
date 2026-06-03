"""`_chain_sessions.json` sealed-state manifest reader/writer.

Per implplan §A.impl.5a (lines 597-672) + plan §A.8. The manifest is
the source of truth for cumulative cost (read by `cap_enforcement.py`
+ §D's planner budget accounting + §F's Sonnet pre-spawn check) and
the source of truth for per-phase trajectory (read by
`completion_summary.py`).

Two writers per implplan A.impl.5a:
- **Chain driver** (this module's `stamp_chain_start`, `stamp_phase_*`,
  `stamp_pause_*`)
- **SessionStart hook** (`splock-session-start.sh`, §G.impl.3) — appends
  bootstrap fields on session-start events.

Both writers wrap the full read-modify-write cycle in flock on
`<plan_dir>/_chain_sessions.json.lock` per Finding 3 + plan §A.8
concurrent-writer lost-update mitigation. §D / §F READ but do not write
the manifest directly (per §A.impl.5a "No third writer").

Atomic write discipline mirrors §B (`write_atomic`) — temp+rename
post-flock so concurrent reads see either the prior or the new version,
never a partial.

Schema-version forward-compat: unknown `schema_version` refuses with
exit code 5 per §B.impl.6 discipline; no silent downgrade.

Schema v2 (current; bumped by `delete_usage_caps`):
    {
        "schema_version": 2,
        "chain_id": str,
        "slug": str,
        "chain_started_at": ISO-8601,
        "wall_clock_cap_seconds": int,
        "phases": [...],
        # CCOR.1 paused-time accumulator (per R-schema-bump):
        "total_paused_seconds": float,    # cumulative completed pause
        "paused_since": str | None,       # ISO-8601 if active pause
    }

`read_manifest` accepts `schema_version ∈ {1, 2}`. For v1 manifests on
disk (pre-`delete_usage_caps` bump), the retired `cost_cap_usd` key is
silently dropped from the returned payload. For both v1 and v2 reads,
missing paused-time fields (`total_paused_seconds`, `paused_since`) are
injected as in-memory defaults (`0.0`, `None`) — the on-disk file is NOT
rewritten until the next write (per R-schema-bump's in-place-upgrade
discipline; the manifest's pre-existing precedent for mid-chain mutation
in `stamp_phase_exit` makes this consistent).
"""

from __future__ import annotations

import contextlib
import datetime
import errno
import fcntl
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal

logger = logging.getLogger(__name__)

MANIFEST_BASENAME = "_chain_sessions.json"
LOCKFILE_BASENAME = "_chain_sessions.json.lock"
SCHEMA_VERSION = 2

# Closed-enum per A.impl.5a "Per-phase field semantics".
ALLOWED_PHASES = (2, 3, 4, 5)
ALLOWED_SOURCE = ("chain", "clear", "compact", "resume")


class ManifestSchemaError(ValueError):
    """Raised on unknown schema_version or other schema-shape violations."""


class ManifestNotFoundError(FileNotFoundError):
    """Raised when read_manifest is called on a non-existent manifest."""


@dataclass(frozen=True)
class PhaseEntry:
    """Single per-phase row in `manifest['phases']`.

    Built up across two writers: bootstrap fields by SessionStart hook;
    exit-time fields by chain driver. Forensic-grade — every field has
    a single canonical writer.
    """

    phase: int
    session_id: str
    slug: str
    chain_id: str
    started_at: str
    source: str
    ended_at: str | None = None
    exit_code: int | None = None
    cost_usd: float | None = None
    model_id: str | None = None


def manifest_path(plan_dir: Path) -> Path:
    """Canonical manifest path for a given plan dir."""
    return plan_dir / MANIFEST_BASENAME


def lockfile_path(plan_dir: Path) -> Path:
    """Canonical flock path for the manifest."""
    return plan_dir / LOCKFILE_BASENAME


# ----------------------------------------------------------------------
# Lock helper (parallel to bin/_jsonl_log/flock_helpers but scoped to
# this manifest's lockfile per the schema doc)
# ----------------------------------------------------------------------


@contextlib.contextmanager
def acquire_exclusive(plan_dir: Path) -> Iterator[int]:
    """Acquire LOCK_EX on `<plan_dir>/_chain_sessions.json.lock`.

    Blocking. Used by both writers (chain driver + SessionStart hook)
    per §A.8 concurrent-writer lost-update mitigation.
    """
    plan_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lockfile_path(plan_dir)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield fd
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ----------------------------------------------------------------------
# Read path (no flock — readers are best-effort consistent on POSIX
# atomic-rename semantics; lost-update mitigation only kicks in on the
# write path)
# ----------------------------------------------------------------------


def read_manifest(plan_dir: Path) -> dict:
    """Read + parse the manifest. Validates schema_version.

    Accepts `schema_version` ∈ {1, 2} per `delete_usage_caps` read-compat
    policy. On `schema_version == 1`, the legacy `cost_cap_usd` top-level
    key is silently dropped from the returned payload so callers see a
    v2-shaped dict (the cap field was retired in `delete_usage_caps`;
    pre-bump manifests on disk may still carry it).

    Per R-schema-bump (CCOR.1): paused-time fields (`total_paused_seconds`,
    `paused_since`) added in CCOR.1's v2 surface extension are injected
    as in-memory defaults (`0.0`, `None`) on both v1 reads AND on v2 reads
    that pre-date this extension. The on-disk file is NOT rewritten — the
    next write that needs the fields (i.e., first `stamp_pause_start`)
    persists them.

    Raises:
        ManifestNotFoundError: if the manifest doesn't exist.
        ManifestSchemaError: if `schema_version` is unknown.
        json.JSONDecodeError: if the file is malformed.
    """
    target = manifest_path(plan_dir)
    if not target.exists():
        raise ManifestNotFoundError(f"manifest not found at {target}")
    text = target.read_text(encoding="utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ManifestSchemaError(
            f"manifest must be a JSON object; got {type(payload).__name__}"
        )
    sv = payload.get("schema_version")
    if sv not in (1, 2):
        raise ManifestSchemaError(
            f"unsupported manifest schema_version={sv!r}; "
            f"this build supports versions 1 and 2 "
            "(v1 retained for read-compat per delete_usage_caps; "
            "see §A.impl.5a forward-compat discipline)"
        )
    if sv == 1:
        # Read-compat: silently drop cost_cap_usd from v1 manifests
        # (delete_usage_caps removed the field; v1 manifests on disk
        # from before the bump may still carry it).
        payload.pop("cost_cap_usd", None)
    # Per R-schema-bump: inject paused-time defaults for any manifest
    # that pre-dates CCOR.1's v2 extension (both v1 and early v2). The
    # on-disk shape is not rewritten until the next write needs them.
    if "total_paused_seconds" not in payload:
        payload["total_paused_seconds"] = 0.0
    if "paused_since" not in payload:
        payload["paused_since"] = None
    return payload


def read_cumulative_cost(plan_dir: Path, chain_id: str | None = None) -> float:
    """Sum `cost_usd` across all phases with matching chain_id.

    Public surface per implplan §A.impl.5a line ~661:
        bin/_chain_overnight/manifest.py::read_cumulative_cost(slug, chain_id)
        # which §D.impl.7 line 2534 cites

    Returns 0.0 if manifest missing or no matching phases.
    """
    try:
        payload = read_manifest(plan_dir)
    except (ManifestNotFoundError, ManifestSchemaError, json.JSONDecodeError):
        return 0.0
    total = 0.0
    for entry in payload.get("phases", []) or []:
        if not isinstance(entry, dict):
            continue
        if chain_id is not None and entry.get("chain_id") != chain_id:
            continue
        cost = entry.get("cost_usd")
        if isinstance(cost, (int, float)):
            total += float(cost)
    return total


def read_manifest_or_empty(plan_dir: Path) -> dict | None:
    """Like `read_manifest` but returns None on absence instead of raising."""
    try:
        return read_manifest(plan_dir)
    except ManifestNotFoundError:
        return None


# ----------------------------------------------------------------------
# Write path — all writers go through these functions
# ----------------------------------------------------------------------


def stamp_chain_start(
    plan_dir: Path,
    *,
    slug: str,
    chain_id: str,
    chain_started_at: str | None = None,
    wall_clock_cap_seconds: int,
) -> dict:
    """Write the initial manifest at chain start.

    Per §A.impl.5a bootstrap shape (v2 schema, post-`delete_usage_caps`):
        - schema_version, chain_id, chain_started_at,
          wall_clock_cap_seconds at top level.
        - phases: [] (empty; populated by SessionStart hook + driver).
        - total_paused_seconds: 0.0 (CCOR.1 paused-time accumulator).
        - paused_since: None (CCOR.1 active-pause marker; ISO when paused).

    Schema bumped 1→2 in `delete_usage_caps` (removes top-level
    `cost_cap_usd`). `read_manifest` retains v1 read-compat. CCOR.1
    extends v2 with the paused-time accumulator pair (per R-schema-bump).

    Acquires flock + atomic-write. If a manifest already exists for this
    chain_id, returns the existing dict (idempotent across resume).
    """
    started = chain_started_at or _now_iso_z()
    with acquire_exclusive(plan_dir):
        existing = read_manifest_or_empty(plan_dir)
        if existing and existing.get("chain_id") == chain_id:
            # Resume / idempotent chain-start. Don't clobber existing phases.
            logger.debug(
                "manifest stamp_chain_start idempotent chain_id=%s", chain_id
            )
            return existing
        payload = {
            "schema_version": SCHEMA_VERSION,
            "chain_id": chain_id,
            "slug": slug,
            "chain_started_at": started,
            "wall_clock_cap_seconds": wall_clock_cap_seconds,
            "phases": [],
            # CCOR.1 paused-time accumulator (R-schema-bump): always
            # initialized on fresh chains; v1 manifests upgraded in-place
            # via read_manifest defaults + the first stamp_pause_start.
            "total_paused_seconds": 0.0,
            "paused_since": None,
        }
        _atomic_write(manifest_path(plan_dir), payload)
        logger.debug(
            "manifest stamp_chain_start chain_id=%s slug=%s", chain_id, slug
        )
        return payload


def append_phase_bootstrap(
    plan_dir: Path,
    *,
    phase: int,
    session_id: str,
    slug: str,
    chain_id: str,
    source: str,
    started_at: str | None = None,
) -> dict:
    """SessionStart hook entry point — append a phase row with bootstrap fields.

    Per §A.impl.5a writer-split table: SessionStart hook (§G.impl.3)
    appends `phase`, `session_id`, `slug`, `chain_id`, `started_at`,
    `source` on every session-start event. Hook-level writes do NOT
    stamp `ended_at` / `exit_code` / `cost_usd` / `model_id` (those are
    unknown at session start).

    The hook itself is a shell script (`splock-session-start.sh`); this
    Python helper exists for the in-process auto-register / resume
    paths and is shared with the hook surface via Python imports if
    needed.
    """
    if phase not in ALLOWED_PHASES:
        raise ValueError(
            f"phase={phase!r} not in allowed range {ALLOWED_PHASES}"
        )
    if source not in ALLOWED_SOURCE:
        raise ValueError(
            f"source={source!r} not in allowed enum {ALLOWED_SOURCE}"
        )
    started = started_at or _now_iso_z()
    with acquire_exclusive(plan_dir):
        payload = read_manifest_or_empty(plan_dir)
        if payload is None:
            raise ManifestNotFoundError(
                "manifest must be created by stamp_chain_start before "
                "phase-bootstrap appends"
            )
        phases = list(payload.get("phases", []) or [])
        phases.append(
            {
                "phase": phase,
                "session_id": session_id,
                "slug": slug,
                "chain_id": chain_id,
                "started_at": started,
                "source": source,
            }
        )
        payload["phases"] = phases
        _atomic_write(manifest_path(plan_dir), payload)
        logger.debug(
            "manifest append_phase_bootstrap phase=%s session_id=%s",
            phase, session_id,
        )
        return payload


def stamp_phase_exit(
    plan_dir: Path,
    *,
    session_id: str,
    ended_at: str | None = None,
    exit_code: int,
    cost_usd: float,
    model_id: str,
) -> dict:
    """Driver-side exit-stamp on phase subprocess termination.

    Per §A.impl.5a writer-split table: driver locates the latest phase
    entry by `session_id` match + missing `ended_at` field; stamps
    `ended_at` / `exit_code` / `cost_usd` / `model_id`.

    Raises ValueError if no matching open phase entry is found (the
    driver should never call this in that state; it's a defensive guard
    against logic errors).
    """
    ended = ended_at or _now_iso_z()
    with acquire_exclusive(plan_dir):
        payload = read_manifest_or_empty(plan_dir)
        if payload is None:
            raise ManifestNotFoundError(
                "manifest must exist before stamp_phase_exit"
            )
        phases = list(payload.get("phases", []) or [])
        # Find the latest entry with matching session_id and missing ended_at.
        match_idx: int | None = None
        for idx in range(len(phases) - 1, -1, -1):
            entry = phases[idx]
            if (
                isinstance(entry, dict)
                and entry.get("session_id") == session_id
                and entry.get("ended_at") in (None, "")
            ):
                match_idx = idx
                break
        if match_idx is None:
            raise ValueError(
                f"no open phase entry found for session_id={session_id!r} "
                f"(all entries for this session already have ended_at stamped)"
            )
        entry = dict(phases[match_idx])
        entry["ended_at"] = ended
        entry["exit_code"] = exit_code
        entry["cost_usd"] = cost_usd
        entry["model_id"] = model_id
        phases[match_idx] = entry
        payload["phases"] = phases
        _atomic_write(manifest_path(plan_dir), payload)
        logger.debug(
            "manifest stamp_phase_exit session_id=%s exit_code=%s cost=$%.4f",
            session_id, exit_code, cost_usd,
        )
        return payload


# ----------------------------------------------------------------------
# CCOR.1 paused-time accumulator (per R-schema-bump + R-cap-injection)
#
# `stamp_pause_start` / `stamp_pause_end` are the manifest-side mutators
# called by `bin/chain-pause` and `bin/chain-resume` respectively. The
# accumulator is read by `bin/_chain_overnight/cap_enforcement.py`'s
# `check_wall_clock_cap` via `read_paused_time_accumulator` so the
# wall-clock cap can subtract paused time from elapsed.
#
# `paused_since` doubles as the "active-pause" marker: non-None means
# the chain is currently paused; the difference between `paused_since`
# and `resumed_at_iso` is added to `total_paused_seconds` on
# `stamp_pause_end`.
# ----------------------------------------------------------------------


def stamp_pause_start(
    plan_dir: Path,
    *,
    chain_id: str,
    paused_at: str | None = None,
) -> dict:
    """Mark the manifest as actively paused.

    Per R-schema-bump + R-cap-injection (CCOR.1). Atomic read-modify-write
    under flock. Sets `paused_since` to the supplied ISO timestamp; does
    NOT touch `total_paused_seconds` (that accumulates on `stamp_pause_end`).

    Refuses if `paused_since` is already non-None (caller bug — pause
    sentinel acquire should have refused this case first via O_EXCL, but
    we defend independently at the manifest layer).

    Args:
        plan_dir: Plan directory containing the manifest.
        chain_id: Expected chain_id; raises ValueError on mismatch.
        paused_at: ISO-8601 UTC timestamp. Defaults to now.

    Returns:
        The updated payload dict (post-write shape).

    Raises:
        ManifestNotFoundError: if the manifest doesn't exist.
        ValueError: if chain_id doesn't match OR pause already active.
    """
    when = paused_at or _now_iso_z()
    with acquire_exclusive(plan_dir):
        payload = read_manifest_or_empty(plan_dir)
        if payload is None:
            raise ManifestNotFoundError(
                "manifest must exist before stamp_pause_start"
            )
        if payload.get("chain_id") != chain_id:
            raise ValueError(
                f"stamp_pause_start chain_id mismatch: "
                f"manifest has {payload.get('chain_id')!r}, "
                f"caller passed {chain_id!r}"
            )
        # Inject defaults defensively (handles v1-on-disk + the rare
        # pre-CCOR.1 v2 reads); read_manifest's in-memory injection only
        # applied to the read-returned dict; the on-disk file may not
        # carry these fields yet.
        if payload.get("paused_since") is not None:
            raise ValueError(
                f"stamp_pause_start refused: pause already active "
                f"(paused_since={payload.get('paused_since')!r}); "
                "call stamp_pause_end first or check sentinel state"
            )
        payload["paused_since"] = when
        # Ensure total_paused_seconds exists on disk after this write
        # (pre-CCOR.1 v2 manifests may lack it; first write upgrades).
        if "total_paused_seconds" not in payload:
            payload["total_paused_seconds"] = 0.0
        _atomic_write(manifest_path(plan_dir), payload)
        logger.debug(
            "manifest stamp_pause_start chain_id=%s paused_at=%s",
            chain_id, when,
        )
        return payload


def stamp_pause_end(
    plan_dir: Path,
    *,
    chain_id: str,
    resumed_at: str | None = None,
) -> float:
    """End an active pause; accumulate delta into `total_paused_seconds`.

    Per R-schema-bump + R-cap-injection (CCOR.1). Atomic read-modify-write
    under flock. Computes delta from `paused_since` ISO to `resumed_at`
    ISO; adds to `total_paused_seconds`; clears `paused_since` to None.

    Refuses if `paused_since` is None (caller bug — no active pause to
    end).

    Args:
        plan_dir: Plan directory containing the manifest.
        chain_id: Expected chain_id; raises ValueError on mismatch.
        resumed_at: ISO-8601 UTC timestamp. Defaults to now.

    Returns:
        The delta (in seconds, float) added to `total_paused_seconds`.

    Raises:
        ManifestNotFoundError: if the manifest doesn't exist.
        ValueError: if chain_id mismatches OR no active pause.
    """
    when = resumed_at or _now_iso_z()
    with acquire_exclusive(plan_dir):
        payload = read_manifest_or_empty(plan_dir)
        if payload is None:
            raise ManifestNotFoundError(
                "manifest must exist before stamp_pause_end"
            )
        if payload.get("chain_id") != chain_id:
            raise ValueError(
                f"stamp_pause_end chain_id mismatch: "
                f"manifest has {payload.get('chain_id')!r}, "
                f"caller passed {chain_id!r}"
            )
        paused_since = payload.get("paused_since")
        if paused_since is None:
            raise ValueError(
                "stamp_pause_end refused: no active pause "
                "(paused_since is None); call stamp_pause_start first"
            )
        # Compute delta. ISO-Z parses cleanly via fromisoformat after the
        # trailing-Z swap (Python <3.11 doesn't accept the Z directly).
        paused_epoch = _iso_z_to_epoch(paused_since)
        resumed_epoch = _iso_z_to_epoch(when)
        delta = max(0.0, resumed_epoch - paused_epoch)
        # Accumulate.
        prev_total = float(payload.get("total_paused_seconds", 0.0) or 0.0)
        payload["total_paused_seconds"] = prev_total + delta
        payload["paused_since"] = None
        _atomic_write(manifest_path(plan_dir), payload)
        logger.debug(
            "manifest stamp_pause_end chain_id=%s delta=%.3fs total=%.3fs",
            chain_id, delta, payload["total_paused_seconds"],
        )
        return delta


def read_paused_time_accumulator(
    plan_dir: Path,
    chain_id: str | None = None,
) -> tuple[float, str | None]:
    """Return `(total_paused_seconds, paused_since)` for cap math.

    Per R-cap-injection (CCOR.1). Consumed by
    `bin/_chain_overnight/cap_enforcement.py::check_wall_clock_cap` (via
    `phase_spawn.py::precheck_caps`) so the wall-clock cap can subtract
    completed-pause time AND the active-pause delta from elapsed.

    Returns `(0.0, None)` for absent/unparseable manifests (defensive —
    same posture as `read_cumulative_cost`'s missing-manifest fallback).

    Args:
        plan_dir: Plan directory containing the manifest.
        chain_id: Optional precision check; if provided and the manifest's
            chain_id differs, returns the defaults rather than the
            mismatched values (cap math should never freeze on the wrong
            chain's pause history).

    Returns:
        A 2-tuple: (total_paused_seconds: float, paused_since: str | None).
    """
    try:
        payload = read_manifest(plan_dir)
    except (ManifestNotFoundError, ManifestSchemaError, json.JSONDecodeError):
        return (0.0, None)
    if chain_id is not None and payload.get("chain_id") != chain_id:
        return (0.0, None)
    total = payload.get("total_paused_seconds", 0.0)
    if not isinstance(total, (int, float)):
        total = 0.0
    paused_since = payload.get("paused_since")
    if paused_since is not None and not isinstance(paused_since, str):
        paused_since = None
    return (float(total), paused_since)


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------


def _atomic_write(target: Path, payload: dict) -> None:
    """Write `payload` to `target` atomically. Presumes flock held."""
    body = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    # Lazy import to keep module-load light.
    from bin._render_plan.atomic_write import write_atomic

    write_atomic(target, body)


def _now_iso_z() -> str:
    """ISO-8601 UTC with trailing Z, second resolution."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _iso_z_to_epoch(iso_z: str) -> float:
    """Parse an ISO-8601 UTC string (trailing Z) to a POSIX epoch float.

    `datetime.fromisoformat` on Python <3.11 doesn't accept the trailing
    `Z` directly; swap it for `+00:00` first.
    """
    normalized = iso_z.replace("Z", "+00:00") if iso_z.endswith("Z") else iso_z
    dt = datetime.datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


__all__ = [
    "ALLOWED_PHASES",
    "ALLOWED_SOURCE",
    "LOCKFILE_BASENAME",
    "MANIFEST_BASENAME",
    "ManifestNotFoundError",
    "ManifestSchemaError",
    "PhaseEntry",
    "SCHEMA_VERSION",
    "acquire_exclusive",
    "append_phase_bootstrap",
    "lockfile_path",
    "manifest_path",
    "read_cumulative_cost",
    "read_manifest",
    "read_manifest_or_empty",
    "read_paused_time_accumulator",
    "stamp_chain_start",
    "stamp_pause_end",
    "stamp_pause_start",
    "stamp_phase_exit",
]
