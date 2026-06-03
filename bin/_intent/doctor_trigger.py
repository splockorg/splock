"""Lazy/opportunistic doctor scheduling (T5 — intent_session_auto_register).

Per research Decision 3: cron/systemd is overkill for dev-phase + WSL2.
We schedule `bin/intent doctor` lazily on operator activity, gated by a
rate-limit timestamp at ``~/.intent_doctor_last_run`` so concurrent
trigger sites never spawn duplicate background doctors per interval
window.

Four trigger sites all flow through :func:`trigger_background`:

  1. ``.claude/hooks/splock-session-start.sh`` — after the auto-register
     subprocess returns.
  2. ``bin/intent register`` CLI — post-success.
  3. ``bin/intent doctor`` manual invocation — calls
     :func:`reset_timestamp` instead (foreground doctor always runs).
  4. ``console/app.py`` Flask ``@app.before_request`` — wrapped in
     try/except so trigger failure never 500s the page.

Concurrent-safety: :func:`should_trigger` holds an exclusive
``fcntl.flock`` on the state file while it reads + writes the
timestamp. The race test fires 10 concurrent callers and asserts
exactly one observes the stale-window + claims the slot. The claim
(timestamp write) happens BEFORE the Popen fork so losing callers see
the fresh timestamp and skip.

Sub-50ms happy-path budget — Popen is detached
(``start_new_session=True`` + stdin/stdout/stderr to ``DEVNULL``), so
the foreground returns immediately after fork.

Settings knob: ``intent.doctor_min_interval_minutes`` (default 60). Read
through ``settings_registry.resolve()`` so the overlay layer stays
authoritative. On DB outage the resolver falls back to the default —
the trigger continues to function (just at the documented default).
"""

from __future__ import annotations

import datetime
import errno
import fcntl
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Operator-local state file. Per research Decision 3: HOME-relative,
# gitignored (no repo-path gitignore needed because Path.home() never
# resolves into the repo tree).
STATE_FILE_NAME = ".intent_doctor_last_run"

# Default fallback when settings_registry can't be reached (lazy import
# or DB outage). Mirrors the spec literal in
# `console/settings_registry.py::intent.doctor_min_interval_minutes`.
_DEFAULT_INTERVAL_MINUTES = 60


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _state_path() -> Path:
    """Resolve the state file path.

    Override via ``INTENT_DOCTOR_STATE_FILE`` env var for tests so the
    race + gate tests can use ``tmp_path`` fixtures instead of writing
    to the operator's real HOME.
    """
    override = os.environ.get("INTENT_DOCTOR_STATE_FILE", "").strip()
    if override:
        return Path(override)
    return Path.home() / STATE_FILE_NAME


def _read_timestamp(path: Path) -> Optional[datetime.datetime]:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    if not text:
        return None
    try:
        # tolerate trailing 'Z'
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return None


def _write_timestamp(path: Path, ts: datetime.datetime) -> None:
    iso = ts.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    # caller already holds the flock; this is a plain truncating write.
    path.write_text(iso + "\n", encoding="utf-8")


def _resolve_interval_minutes() -> int:
    """Framework-internal resolve with documented-default fallback.

    SC-C #3 — routes through :mod:`bin._intent.settings`, which has
    no MySQL / ``src.DAL`` dependency. Defensive fallback to the
    literal default mirrors the legacy contract: any failure path
    returns ``_DEFAULT_INTERVAL_MINUTES``.
    """
    try:
        from . import settings as intent_settings
        return int(intent_settings.resolve(
            "intent.doctor_min_interval_minutes",
            _DEFAULT_INTERVAL_MINUTES,
        ))
    except Exception:  # noqa: BLE001
        logger.debug(
            "intent.doctor_min_interval_minutes resolve failed; "
            "returning default %d", _DEFAULT_INTERVAL_MINUTES,
            exc_info=True,
        )
        return _DEFAULT_INTERVAL_MINUTES


def should_trigger(now: Optional[datetime.datetime] = None) -> bool:
    """Return True iff a background doctor should fire now.

    Concurrent-safe: holds an exclusive ``fcntl.flock`` on the state
    file while reading + writing the timestamp. If True, the timestamp
    has ALREADY been written (claim-before-fork discipline) so losing
    concurrent callers see the fresh stamp and return False.

    On any I/O / OS error, returns False (fail-closed — better to skip
    a sweep than crash the trigger site).
    """
    now = now or _now_utc()
    interval_min = _resolve_interval_minutes()
    state = _state_path()
    try:
        state.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    # Open in a+ mode so the file is created if missing; the flock
    # gates the read/write critical section.
    try:
        fh = state.open("a+", encoding="utf-8")
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            return False
        # Read the timestamp from the current file contents (re-read
        # after acquiring the lock; another caller may have just
        # claimed the slot).
        last = _read_timestamp(state)
        if last is not None:
            # Normalize to UTC-aware for comparison.
            if last.tzinfo is None:
                last = last.replace(tzinfo=datetime.timezone.utc)
            delta = now - last
            if delta < datetime.timedelta(minutes=interval_min):
                return False
        # Claim the slot BEFORE forking so concurrent callers see the
        # fresh timestamp once they acquire the lock.
        try:
            _write_timestamp(state, now)
        except OSError:
            return False
        return True
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            fh.close()
        except OSError:
            pass


def reset_timestamp(now: Optional[datetime.datetime] = None) -> None:
    """Unconditionally write the current timestamp.

    Used by the manual `bin/intent doctor` handler (research
    Decision 3 trigger #3) — the foreground doctor always runs, and
    the timestamp reset ensures subsequent lazy triggers honor the
    fresh window.
    """
    now = now or _now_utc()
    state = _state_path()
    try:
        state.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    try:
        fh = state.open("a+", encoding="utf-8")
    except OSError:
        return
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            pass
        try:
            _write_timestamp(state, now)
        except OSError:
            pass
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            fh.close()
        except OSError:
            pass


def _repo_root() -> Path:
    # bin/_intent/doctor_trigger.py → REPO_ROOT (two parents up).
    return Path(__file__).resolve().parents[2]


def _spawn_background_doctor() -> None:
    """Fork ``bin/intent doctor`` detached. Returns immediately.

    Output is dropped to DEVNULL. ``start_new_session=True`` puts the
    child in a new process group so the parent's exit doesn't propagate
    a SIGHUP. ``close_fds=True`` keeps inherited file descriptors out
    of the child.
    """
    repo_root = _repo_root()
    bin_intent = repo_root / "bin" / "intent"
    if not bin_intent.exists():
        return
    try:
        subprocess.Popen(
            [str(bin_intent), "doctor"],
            cwd=str(repo_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        # Fork / exec failure — fail-closed. The next trigger window
        # will retry.
        pass


def trigger_background() -> bool:
    """Lazy-trigger entry point.

    Returns True iff a background doctor was actually spawned (i.e.
    the gate fired AND the Popen attempt was made). Returns False on
    skip OR on rate-limited / IO-error paths.

    Callers in latency-sensitive paths (SessionStart hook, Flask
    middleware) must wrap this in try/except — but the function is
    defensive enough that it should never raise. The wrap exists so
    a hypothetical bug in this module can't take down its caller.
    """
    if not should_trigger():
        return False
    _spawn_background_doctor()
    return True


__all__ = [
    "STATE_FILE_NAME",
    "should_trigger",
    "reset_timestamp",
    "trigger_background",
]
