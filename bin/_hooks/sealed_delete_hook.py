"""Python entry point for the chain-sealed-state-delete-block hook.

Always-on PreToolUse refusal of agent-side deletes against sealed paths.
Finding 2 dual-altitude with driver-side `pre_stage.py`.

Per plan §G.3 + implplan §G.impl.5.

Both this hook AND `bin/_chain_overnight/pre_stage.py` read the same
`.claude/hooks/sealed_paths.txt` — verifying path-set identity is a
post-section Sonnet review item per orchestrator §4a.5.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from bin._hooks.pattern_detect import extract_delete_targets
from bin._hooks.sealed_paths import is_sealed, load_sealed_paths


def _emit_deny(reason: str) -> None:
    envelope = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(envelope) + "\n")
    sys.stdout.flush()


def _hook_log(action: str, message: str) -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    binpath = repo_root / "bin" / "hook-log"
    if not binpath.exists():
        return
    try:
        subprocess.run(
            [str(binpath), "chain-sealed-state-delete-block", action, message[:200]],
            timeout=5,
            check=False,
            capture_output=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _candidate_paths_from_event(data: dict) -> list[str]:
    """Extract candidate delete-target paths from a PreToolUse event."""
    tool_name = data.get("tool_name") or data.get("tool") or ""
    tool_input = data.get("tool_input", {}) if isinstance(data, dict) else {}
    if not isinstance(tool_input, dict):
        return []

    if tool_name == "Bash":
        command = tool_input.get("command") or ""
        return extract_delete_targets(command)

    if tool_name in ("Edit", "Write"):
        new_str = tool_input.get("new_string") or tool_input.get("content") or ""
        path = tool_input.get("file_path") or tool_input.get("path") or ""
        if not path:
            return []
        # Delete-equivalent: empty / whitespace-only AND target file exists
        # with content. We can only check the first condition from stdin;
        # the second (target nonempty) check requires filesystem access,
        # which we do best-effort below.
        if new_str.strip() == "":
            try:
                target = Path(path)
                if target.exists() and target.is_file():
                    if target.stat().st_size > 0:
                        return [path]
            except OSError:
                # Filesystem inaccessible — fail safe: treat as delete candidate.
                return [path]

    return []


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        data = {}

    candidates = _candidate_paths_from_event(data)
    if not candidates:
        _hook_log("ok", "no delete candidates")
        return 0

    # Load canonical inventory. SPLOCK_SEALED_PATHS_FILE override for tests.
    # Default: prefer cwd-relative `.claude/hooks/sealed_paths.txt`; if
    # absent, fall back to the repo where this module lives.
    override = os.environ.get("SPLOCK_SEALED_PATHS_FILE", "").strip()
    if override:
        sealed_file = Path(override)
    else:
        cwd_candidate = Path(".claude/hooks/sealed_paths.txt")
        if cwd_candidate.exists():
            sealed_file = cwd_candidate
        else:
            sealed_file = (
                Path(__file__).resolve().parent.parent.parent
                / ".claude" / "hooks" / "sealed_paths.txt"
            )
    try:
        patterns = load_sealed_paths(sealed_file)
    except FileNotFoundError:
        _hook_log("error", "sealed_paths.txt missing; allowing")
        return 0

    for candidate in candidates:
        matched, pattern = is_sealed(candidate, patterns)
        if matched:
            reason = (
                f"cannot delete sealed-state file {candidate!r}. "
                f"Sealed-state files are append-only or CLI-managed. "
                f"Matched pattern: {pattern}. Per plan §G.3 / §5.B."
            )
            _emit_deny(reason)
            _hook_log("blocked", f"target={candidate} pattern={pattern}")
            return 0

    _hook_log("ok", f"candidates={len(candidates)} (none sealed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
