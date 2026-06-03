"""Python entry point for the splock-session-start SessionStart hook.

Per plan §G.1 + implplan §G.impl.3.

Always returns 0 (SessionStart is non-permission-gating). Failures emit
`error` rows to hook-log for morning-review forensic surfacing.

T3 (intent_session_auto_register) — STDOUT envelope contract:
  After the chain-manifest phase, this module writes a single line of
  JSON to stdout shaped like
  `{"claude_session_id": "<id or empty string>", "source": "..."}`.
  The shell hook (`.claude/hooks/splock-session-start.sh`) captures the
  line, parses out the claude_session_id, and threads it through to
  `bin/intent register --claude-session-id <id>`. Empty / missing
  session_id → empty string (the shell hook treats empty as "no
  claude session id to pass through").
"""

from __future__ import annotations

import datetime
import fcntl
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _hook_log(action: str, message: str) -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    binpath = repo_root / "bin" / "hook-log"
    if not binpath.exists():
        return
    try:
        subprocess.run(
            [str(binpath), "splock-session-start", action, message[:200]],
            timeout=5,
            check=False,
            capture_output=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _now_iso_z() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _atomic_write_json(target: Path, payload: dict) -> None:
    """Write JSON via temp+rename per cross-cutting atomic-write discipline."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=target.name + ".",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _append_phase_entry(
    plan_dir: Path,
    phase: int | None,
    session_id: str,
    slug: str,
    chain_id: str,
    source: str,
) -> None:
    """Acquire flock; read manifest; append; atomic-rename write; release.

    Per cross-cutting `flock` discipline (lines 287-292).
    """
    plan_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = plan_dir / "_chain_sessions.json"
    lock_path = plan_dir / "_chain_sessions.json.lock"

    # Open / create the lockfile.
    with lock_path.open("a+") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            # Read existing manifest (bootstrap if missing).
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if not isinstance(manifest, dict):
                        manifest = {"phases": []}
                except (OSError, json.JSONDecodeError):
                    manifest = {"phases": []}
            else:
                manifest = {"phases": []}
            phases = manifest.setdefault("phases", [])
            entry = {
                "phase": phase,
                "session_id": session_id,
                "slug": slug,
                "chain_id": chain_id,
                "started_at": _now_iso_z(),
                "source": source,
            }
            phases.append(entry)
            _atomic_write_json(manifest_path, manifest)
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def _emit_shell_envelope(session_id: str, source: str) -> None:
    """T3 — emit a single JSON line to stdout for the shell hook to parse.

    The shell hook (`.claude/hooks/splock-session-start.sh`) uses this line
    to recover the Claude Code session_id from the envelope (because
    stdin is consumed by this Python entry, the shell cannot re-read it).
    Best-effort write; if stdout is closed (rare), swallow silently —
    the chain-manifest write already succeeded.

    GATED by `SPLOCK_SESSION_START_SHELL_ENVELOPE=1` env var so the
    Python entry stays silent when invoked outside the T3-aware shell
    hook (backward compat — the pre-T3 shell hook piped Python stdout
    straight through and tests asserted no stdout). The T3 shell hook
    sets this env var before invoking the Python entry; old shell
    hook does not, so old behavior is preserved.
    """
    if os.environ.get("SPLOCK_SESSION_START_SHELL_ENVELOPE", "").strip() != "1":
        return
    try:
        line = json.dumps(
            {
                "claude_session_id": session_id or "",
                "source": source or "",
            },
            sort_keys=True,
        )
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    except (OSError, ValueError):
        pass


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        data = {}

    # T3 (intent_session_auto_register): extract session_id from envelope
    # regardless of slug presence — the shell hook needs it for both the
    # chain-manifest leg AND the auto-register subprocess invocation in
    # non-chain interactive sessions.
    session_id = (data.get("session_id") or "").strip() if isinstance(data, dict) else ""
    source = (data.get("source") or "startup").strip() if isinstance(data, dict) else "startup"

    slug = os.environ.get("SPLOCK_PLAN_SLUG", "").strip()
    if not slug:
        _hook_log("ok", "non-chain")
        # T3: still emit the envelope so the shell hook can pass
        # claude_session_id through to auto-register for non-chain
        # interactive sessions.
        _emit_shell_envelope(session_id, source)
        return 0

    chain_id = os.environ.get("SPLOCK_CHAIN_ID", "").strip()
    phase_raw = os.environ.get("SPLOCK_PHASE", "").strip()
    try:
        phase = int(phase_raw) if phase_raw else None
    except ValueError:
        phase = None

    plan_dir = Path("docs") / "plans" / slug
    try:
        _append_phase_entry(plan_dir, phase, session_id, slug, chain_id, source)
        _hook_log(
            "ok",
            f"session_id={session_id} chain_id={chain_id} phase={phase} source={source}",
        )
    except (OSError, json.JSONDecodeError) as exc:
        _hook_log("error", f"append_failed: {exc}")
    # T3: emit the shell envelope after the chain-manifest leg too.
    _emit_shell_envelope(session_id, source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
