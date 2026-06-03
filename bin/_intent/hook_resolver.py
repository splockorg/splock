"""PreToolUse hook resolver for `.claude/hooks/intent-on-first-edit.sh`.

Per implplan §P.impl.9. Reads PreToolUse JSON from stdin, applies the
three refusal cases (lines 8849-8853), and prints a `permissionDecision`
JSON to stdout.

Read-only contract: hook reads `docs/intent/intent_local.jsonl` only;
NEVER writes to the registry. Best-effort audit row via `bin/hook-log`.

Always-on (no chain-context gating) per P.impl.17 #2 ratification.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from . import jsonl_writer


def _hostname() -> str:
    return socket.gethostname()


def _resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _hook_log(action: str, msg: str) -> None:
    """Append an audit row via `bin/hook-log`. Best-effort."""
    repo = _resolve_repo_root()
    bin_path = repo / "bin" / "hook-log"
    if not bin_path.exists():
        return
    try:
        subprocess.run(
            [str(bin_path), "intent-on-first-edit", action, msg],
            cwd=str(repo),
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _ttl_minutes() -> int:
    """Resolve ``intent.ttl_minutes`` via the framework-internal resolver
    (5-min cached); fall back to 240 when unavailable.

    SC-C #3 — :mod:`bin._intent.settings` replaces the host
    ``console.settings_registry`` + ``src.DAL`` pair.
    """
    try:
        from . import settings as intent_settings
        return int(intent_settings.resolve("intent.ttl_minutes", 240))
    except Exception:  # noqa: BLE001
        return 240


def _soft_warn_enabled() -> bool:
    """Resolve ``intent.soft_warning_path_prefix_enabled`` via the
    framework-internal resolver. Defaults to True (legacy contract)."""
    try:
        from . import settings as intent_settings
        return bool(intent_settings.resolve(
            "intent.soft_warning_path_prefix_enabled", True
        ))
    except Exception:  # noqa: BLE001
        return True


def _resolve_session(host: str) -> Optional[dict]:
    """Resolve the agent's active session_id.

    Precedence:
      (a) $SPLOCK_INTENT_SESSION_ID env (set by chain-driver auto-register).
      (b) Most-recent active row matching host AND last-activity inside ttl.
    """
    sid = os.environ.get("SPLOCK_INTENT_SESSION_ID")
    if sid:
        row = jsonl_writer.find_by_session_id(sid)
        if row and not row.get("closed_at"):
            return row

    active = jsonl_writer.find_active_for_host(host)
    if not active:
        return None
    # No ttl filtering in fall-back path: caller already cleared closed.
    # Return most-recent by last_activity_at lexicographically (ISO Z stamps sort).
    active.sort(key=lambda r: r.get("last_activity_at") or "", reverse=True)
    return active[0]


def _file_under_paths(file_path: str, paths: list[str]) -> bool:
    """Return True when file_path is covered by any glob in paths."""
    import fnmatch
    for g in paths:
        if not g:
            continue
        if fnmatch.fnmatch(file_path, g):
            return True
        # Treat dir/** as prefix; also recognize bare-dir prefix matching.
        base = g.split("*", 1)[0].rstrip("/")
        if base and file_path.startswith(base + "/"):
            return True
        if base and file_path == base:
            return True
    return False


def _emit_decision(
    decision: str, reason: str, *, stdout=None,
) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    (stdout or sys.stdout).write(json.dumps(payload, sort_keys=True) + "\n")


def evaluate(input_payload: dict) -> dict:
    """Return a decision dict {decision, reason, case} without writing.

    Pure-function entry for unit tests.
    """
    tool_input = input_payload.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not file_path:
        return {"decision": "allow", "reason": "no file_path", "case": None}

    host = _hostname()
    active_rows = jsonl_writer.find_active_for_host(host)

    # Collect all other sessions claiming this path (non-current).
    current = _resolve_session(host)
    current_sid = current.get("session_id") if current else None

    conflicting: list[str] = []
    for r in active_rows:
        if r.get("session_id") == current_sid:
            continue
        cps = r.get("claimed_paths") or []
        if isinstance(cps, str):
            try:
                cps = json.loads(cps)
            except json.JSONDecodeError:
                cps = []
        if _file_under_paths(file_path, cps):
            conflicting.append(r.get("session_id"))

    # Case 1: no current session AND target under another session's paths.
    if current is None and conflicting:
        return {
            "decision": "deny",
            "reason": (
                f"case=1: no bin/intent register for current session; target={file_path} "
                f"is under active session(s) {conflicting}. resolve via "
                f"'bin/intent pivot <sid>' or 'bin/intent register --area <new>'."
            ),
            "case": 1,
            "conflicting": conflicting,
        }
    # Case 3: current session is read_only_*; refuse any Edit/Write.
    if current and current.get("kind") in ("read_only_recon", "read_only_review"):
        return {
            "decision": "deny",
            "reason": (
                f"case=3: session {current_sid} declared kind={current.get('kind')}; "
                f"Edit/Write refused on target={file_path}."
            ),
            "case": 3,
        }
    # Case 2: registered but OUTSIDE its claimed_paths.
    if current is not None:
        cps = current.get("claimed_paths") or []
        if isinstance(cps, str):
            try:
                cps = json.loads(cps)
            except json.JSONDecodeError:
                cps = []
        if not _file_under_paths(file_path, cps) and _soft_warn_enabled():
            # T4 (intent_session_auto_register): when the current session
            # has an EMPTY claimed_paths (the auto-register default),
            # gate the soft-warn on `intent.warn_on_unscoped_session`.
            # Default knob value is False → silent-allow; suppresses the
            # false-positive "outside declared scope" warn for every
            # edit in a session whose scope is intentionally unset.
            # The legacy `intent.soft_warning_path_prefix_enabled` knob
            # still gates non-empty-paths warns.
            if not cps:
                from . import settings as intent_settings
                if not intent_settings.resolve_warn_on_unscoped_session():
                    return {
                        "decision": "allow",
                        "reason": (
                            "case=2 suppressed: session "
                            f"{current_sid} has empty claimed_paths "
                            "and intent.warn_on_unscoped_session=False"
                        ),
                        "case": None,
                    }
            return {
                "decision": "warn",
                "reason": (
                    f"case=2: session {current_sid} declared paths={cps}; "
                    f"target={file_path} is OUTSIDE the declared scope. "
                    f"consider 'bin/intent update {current_sid} --paths <...>'."
                ),
                "case": 2,
            }

    return {"decision": "allow", "reason": "no conflict", "case": None}


def main(stdin=None, stdout=None) -> int:
    raw = (stdin or sys.stdin).read()
    if not raw.strip():
        _emit_decision("allow", "empty hook input", stdout=stdout)
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        _emit_decision("allow", "non-json hook input", stdout=stdout)
        return 0

    verdict = evaluate(payload)
    decision = verdict["decision"]
    reason = verdict["reason"]
    case = verdict.get("case")

    if decision == "deny":
        _hook_log("blocked", f"case={case}; reason={reason[:200]}")
    elif decision == "warn":
        _hook_log("flagged", f"case={case}; reason={reason[:200]}")
    else:
        _hook_log("ok", "passthrough")

    _emit_decision(decision, reason, stdout=stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
