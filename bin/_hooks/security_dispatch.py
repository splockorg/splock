"""Umbrella PreToolUse dispatcher — central routing per §G.impl.10.

Reads PreToolUse event JSON on stdin, classifies the tool name +
optional command shape, then forks (sequentially, first-deny-wins) to
the appropriate sub-hook script. The sub-hook's stdout (deny envelope
or empty) is echoed verbatim to our stdout; we exit 0 in all cases.

Sub-hook dispatch table (per implplan §G.impl.10):

    Order  Sub-hook                                  Trigger
    1      .claude/hooks/sealed-paths.sh             tool ∈ {Edit, Write, Read}
    2      .claude/hooks/package-safety.sh           tool = Bash AND install pattern
    3      .claude/hooks/safe-ddl.sh                 tool = Bash AND DDL pattern
    4      .claude/hooks/guardrail-spawn.sh          tool = Task (if installed)

If a sub-hook emits stdout (deny envelope) we propagate it and stop;
later sub-hooks are NOT invoked. If a sub-hook is missing on disk we
skip it (allowing the substrate to ship before guardrail-spawn lands).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bin._hooks.pattern_detect import is_install_command, scan_ddl_command


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _hook_log(action: str, message: str) -> None:
    binpath = REPO_ROOT / "bin" / "hook-log"
    if not binpath.exists():
        return
    try:
        subprocess.run(
            [str(binpath), "security-dispatch", action, message[:200]],
            timeout=5,
            check=False,
            capture_output=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _run_subhook(script_path: Path, hook_input: str) -> tuple[int, str]:
    """Invoke a sub-hook with the event JSON on stdin; return (rc, stdout).

    Stderr is discarded for the dispatcher's purposes; sub-hooks emit
    their own audit-trail via bin/hook-log.
    """
    if not script_path.exists():
        return (0, "")
    try:
        proc = subprocess.run(
            ["bash", str(script_path)],
            input=hook_input,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return (proc.returncode, proc.stdout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        # Sub-hook failure → dispatcher allows (fail-open in dispatch;
        # each sub-hook's own fail-closed/open contract is its concern).
        _hook_log("error", f"subhook_failed: {script_path.name} {exc}")
        return (0, "")


def _is_deny_envelope(stdout: str) -> bool:
    if not stdout.strip():
        return False
    try:
        payload = json.loads(stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, ValueError, IndexError):
        return False
    if not isinstance(payload, dict):
        return False
    output_block = payload.get("hookSpecificOutput")
    if not isinstance(output_block, dict):
        return False
    return output_block.get("permissionDecision") == "deny"


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        data = {}

    tool_name = data.get("tool_name") or data.get("tool") or ""
    tool_input = data.get("tool_input", {}) if isinstance(data, dict) else {}
    command = ""
    if isinstance(tool_input, dict):
        command = tool_input.get("command", "") or ""

    hooks_dir = REPO_ROOT / ".claude" / "hooks"
    dispatch_log: list[str] = []

    # 1. sealed-paths.sh (Edit / Write / Read).
    if tool_name in ("Edit", "Write", "Read"):
        rc, out = _run_subhook(hooks_dir / "sealed-paths.sh", raw)
        dispatch_log.append(f"sealed-paths rc={rc}")
        if _is_deny_envelope(out):
            sys.stdout.write(out)
            sys.stdout.flush()
            _hook_log("blocked", f"routed=sealed-paths {' '.join(dispatch_log)}")
            return 0

    # 2. package-safety.sh (Bash install).
    if tool_name == "Bash" and is_install_command(command):
        rc, out = _run_subhook(hooks_dir / "package-safety.sh", raw)
        dispatch_log.append(f"package-safety rc={rc}")
        if _is_deny_envelope(out):
            sys.stdout.write(out)
            sys.stdout.flush()
            _hook_log("blocked", f"routed=package-safety {' '.join(dispatch_log)}")
            return 0

    # 3. safe-ddl.sh (Bash DDL).
    if tool_name == "Bash" and scan_ddl_command(command) is not None:
        rc, out = _run_subhook(hooks_dir / "safe-ddl.sh", raw)
        dispatch_log.append(f"safe-ddl rc={rc}")
        if _is_deny_envelope(out):
            sys.stdout.write(out)
            sys.stdout.flush()
            _hook_log("blocked", f"routed=safe-ddl {' '.join(dispatch_log)}")
            return 0

    # 4. guardrail-spawn.sh (Task) — optional, inherited per plan §G.6.
    if tool_name == "Task":
        rc, out = _run_subhook(hooks_dir / "guardrail-spawn.sh", raw)
        dispatch_log.append(f"guardrail-spawn rc={rc}")
        if _is_deny_envelope(out):
            sys.stdout.write(out)
            sys.stdout.flush()
            _hook_log("blocked", f"routed=guardrail-spawn {' '.join(dispatch_log)}")
            return 0

    _hook_log("ok", f"tool={tool_name} no_refusal {' '.join(dispatch_log) or 'no-routes'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
