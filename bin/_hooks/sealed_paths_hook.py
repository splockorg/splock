"""Python entry point for the sealed-paths PreToolUse hook.

Reads PreToolUse event JSON on stdin, classifies the candidate path
against `sealed_paths.txt`, and emits either:
  - empty stdout + exit 0 (allow)
  - JSON deny envelope on stdout + exit 0 (refuse)

Per plan §G.7.2 + §G.7.3; implplan §G.impl.9.

Audit-trail emit via `bin/hook-log`. Best-effort; failure to log does
not fail the hook (the hook's decision is the load-bearing output).

plan_surgical_amend §SC7 (T7) — CLOSED-keyed plan-doc lock
----------------------------------------------------------
Historically the plan-doc carve-out kept `<slug>_plan.{json,md}` freely
editable while drafting and re-locked it only once the plan became an
ACTIVE downstream object (its `<slug>_orchestrator.json` existed). That
keyed the lock off orchestrator EXISTENCE.

With `bin/plan --amend` now shipped (a surgical, deterministic, byte-
preserving keyed-patch mutation path), a RAW Edit/Write to a plan
substrate doc is no longer the right tool at any lifecycle stage — even
during drafting it bypasses the audit log + op-bounding + post-apply
re-validation that `--amend` provides. So the lock is re-keyed:

  * RAW Edit/Write on `<slug>_plan.{json,md}` is DENIED throughout the
    draft + active/coding phases. The sanctioned mutation is
    `bin/plan --amend` (a Python write, which bypasses this PreToolUse
    hook — see the Python-IO-bypasses-hook contract in
    `bin/_hooks/plan_render_on_edit.py`).
  * Reads are ALWAYS allowed.
  * The deny message ROUTES the operator to the right mutation path:
    `bin/plan --amend` while the plan is ACTIVE (orchestrator not
    closed), and the wholesale `bin/plan --reopen` route once the plan
    is CLOSED (frozen) — a closed plan is sealed, so surgical amend is
    off the table and re-opening is the deliberate escape hatch.

The CLOSED signal is read via `closed_state.is_closed(plan_dir)` (T4),
which checks ONLY for the `_orchestrator_closed.lock` sentinel marker —
it never parses `_state.json`, so a missing / unparseable / half-written
(partial) / lock-contended state file can never crash this hook. The
failure-mode table is therefore:

    signal state   ->  is_closed()  ->  routing
    -------------------------------------------------------------
    marker missing ->  False        ->  NOT-closed -> cite --amend
    unparseable    ->  False*       ->  NOT-closed -> cite --amend
    locked         ->  False*       ->  NOT-closed -> cite --amend
    partial        ->  False*       ->  NOT-closed -> cite --amend
    marker present ->  True         ->  CLOSED     -> cite --reopen

    (* `is_closed` only stat()s the marker; an unreadable/locked/partial
       `_state.json` is irrelevant to it, so all degrade to NOT-closed,
       the conservative side: raw edit stays DENIED and the operator is
       sent to the surgical `--amend` path, never silently allowed.)

Either way the RAW edit is DENIED; closed-state only steers WHICH
sanctioned path the deny message cites (anti-retry-loop).
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
from pathlib import Path

from bin._hooks.pattern_detect import scan_settings_content
from bin._hooks.sealed_paths import is_sealed, load_sealed_paths


# Plan substrate docs (<slug>_plan.json / <slug>_plan.md). A RAW Edit/Write
# to one of these is DENIED at every lifecycle stage (draft + active/coding);
# the sanctioned mutation is `bin/plan --amend` (which is a Python write and
# so bypasses this PreToolUse hook). Reads are always allowed. State files
# (_state.json, *_orchestrator.json, logs, locks) stay sealed unconditionally
# via the generic is_sealed() check below. The `*/`-prefixed globs catch
# absolute paths (fnmatch's `*` spans `/`).
_PLAN_DOC_GLOBS = (
    "docs/plans/*/*_plan.json",
    "docs/plans/*/*_plan.md",
    "*/docs/plans/*/*_plan.json",
    "*/docs/plans/*/*_plan.md",
)


def _is_plan_doc(file_path: str) -> bool:
    """True iff `file_path` is a plan substrate doc (<slug>_plan.{json,md})."""
    p = file_path[2:] if file_path.startswith("./") else file_path
    return any(fnmatch.fnmatch(p, glob) for glob in _PLAN_DOC_GLOBS)


def _plan_is_closed(file_path: str) -> bool:
    """True iff the plan owning `file_path` is CLOSED (frozen).

    A plan is closed once `bin/update_orchestrator` has written its
    `_orchestrator_closed.lock` sentinel (the durable idempotency token —
    see `bin/_update_orchestrator/closed_state.is_closed`, T4). This reads
    ONLY the marker's existence; it never parses `_state.json`, so a
    missing / unparseable / locked / partial state file cannot raise here.

    Fail-CONSERVATIVE: on any resolution error, treat the plan as NOT
    closed (the safe side for routing — the raw edit is denied regardless,
    and the operator is steered to the surgical `bin/plan --amend` path
    rather than the heavier `--reopen`). A sealed-paths mechanism must
    never crash; here a crash-free NOT-closed verdict is the conservative
    default. Relative paths resolve against the process cwd, matching how
    the hook runs (and how the fake-repo tests anchor via ``cwd=``).
    """
    try:
        from bin._update_orchestrator.closed_state import is_closed

        plan_dir = Path(file_path).parent
        return bool(is_closed(plan_dir))
    except Exception:
        # Any import/resolution failure → NOT-closed (route to --amend).
        return False


def _plan_doc_deny_reason(tool_name: str, file_path: str) -> str:
    """Build the deny message for a raw Edit/Write on a plan substrate doc.

    Routes the operator to the sanctioned mutation path, keyed off
    closed-state: `bin/plan --amend` while ACTIVE (anti-retry-loop), the
    wholesale `bin/plan --reopen` once CLOSED. Always cites the concrete
    command so a raw retry loop does not form.
    """
    if _plan_is_closed(file_path):
        return (
            f"sealed-paths refused {tool_name} on {file_path!r}: this plan "
            f"is CLOSED (its _orchestrator_closed.lock sentinel is present) "
            f"— a frozen plan is sealed and is NOT surgically amendable. To "
            f"deliberately re-derive it, re-open downstream first: "
            f"`bin/plan --reopen <slug>` (the wholesale regeneration path). "
            f"Raw Edit/Write of the plan substrate is never the sanctioned "
            f"mutation; Reads are always allowed."
        )
    return (
        f"sealed-paths refused {tool_name} on {file_path!r}: raw Edit/Write "
        f"of a plan substrate doc is not the sanctioned mutation path. Fold "
        f"your change through `bin/plan --amend <slug> --directive \"...\"` — "
        f"the surgical, deterministic, byte-preserving keyed-patch path that "
        f"audit-logs the directive, bounds drift, and re-validates the "
        f"result as a plan (plan_surgical_amend §SC6/§SC7). `--amend` is a "
        f"Python write and so is NOT blocked by this hook. Reads are always "
        f"allowed; for a wholesale re-derivation use `bin/plan --reopen "
        f"<slug>` instead."
    )


def _emit_deny(reason: str) -> None:
    """Write the JSON deny envelope to stdout per Claude Code PreToolUse."""
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
    """Best-effort audit-trail emit via bin/hook-log."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    binpath = repo_root / "bin" / "hook-log"
    if not binpath.exists():
        return
    try:
        subprocess.run(
            [str(binpath), "sealed-paths", action, message[:200]],
            timeout=5,
            check=False,
            capture_output=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        data = {}

    tool_name = data.get("tool_name") or data.get("tool") or ""
    tool_input = data.get("tool_input", {}) if isinstance(data, dict) else {}
    file_path = ""
    content = ""
    if isinstance(tool_input, dict):
        file_path = tool_input.get("file_path") or tool_input.get("path") or ""
        content = (
            tool_input.get("content")
            or tool_input.get("new_string")
            or ""
        )

    # Activation guard — only Read / Edit / Write.
    if tool_name not in ("Read", "Edit", "Write"):
        _hook_log("ok", f"tool={tool_name} (out-of-scope)")
        return 0

    # --- Plan-doc CLOSED-keyed lock (plan_surgical_amend §SC7 / T7) ---
    # A RAW Edit/Write to a plan substrate doc is DENIED at every lifecycle
    # stage; `bin/plan --amend` is the sanctioned mutation (a Python write,
    # which bypasses this PreToolUse hook). This MUST run before the generic
    # is_sealed() check below, because the plan globs remain in
    # sealed_paths.txt (so the delete-block hook still guards against
    # rm/truncate) and is_sealed() would otherwise deny with the wrong
    # (non-routing) message. Reads are always allowed; the deny message is
    # routed by closed-state (--amend while active, --reopen once closed).
    if _is_plan_doc(file_path):
        if tool_name == "Read":
            _hook_log("ok", f"plan-doc read allowed path={file_path}")
            return 0
        reason = _plan_doc_deny_reason(tool_name, file_path)
        _emit_deny(reason)
        closed = "closed" if _plan_is_closed(file_path) else "active"
        _hook_log("blocked", f"plan-doc raw-edit ({closed}) path={file_path}")
        return 0

    # Load canonical inventory. Resolution (incl. the SPLOCK_SEALED_PATHS_FILE
    # test override) lives in one place — this module used to hardcode the
    # `.claude/hooks/` layout, which does not exist in this fork, so it always
    # took the `FileNotFoundError -> allow` branch below and failed OPEN.
    from bin._hooks import sealed_paths_file

    sealed_file = sealed_paths_file()
    try:
        patterns = load_sealed_paths(sealed_file)
    except FileNotFoundError:
        # No inventory → no defense. Emit a forensic note; allow.
        _hook_log("error", "sealed_paths.txt missing; allowing")
        return 0

    # Path-based seal check.
    matched, pattern = is_sealed(file_path, patterns)
    if matched:
        reason = (
            f"sealed-paths refused {tool_name} on sealed path {file_path!r} "
            f"(matched pattern: {pattern}). Sealed-state files are append-only "
            f"or CLI-managed; user-home credential files are off-limits to "
            f"agents. v2.7 plan §5.B + §G.7.2."
        )
        _emit_deny(reason)
        _hook_log("blocked", f"target={file_path} pattern={pattern}")
        return 0

    # Content-based check for .claude/settings.json (§G.7.3).
    if tool_name in ("Edit", "Write") and file_path.endswith(".claude/settings.json"):
        if scan_settings_content(content):
            reason = (
                "sealed-paths refused: introducing "
                "\"enableAllProjectMcpServers\": true into "
                ".claude/settings.json is forbidden (CVE-2025-59536). "
                "Per-project MCP servers MUST be opted in individually "
                "via enabledMcpjsonServers."
            )
            _emit_deny(reason)
            _hook_log("blocked", f"target={file_path} pattern=enableAllProjectMcpServers")
            return 0

    # Allow path.
    _hook_log("ok", f"tool={tool_name} path={file_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
