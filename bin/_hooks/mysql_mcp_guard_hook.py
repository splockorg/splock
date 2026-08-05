"""Python entry point for the mysql-mcp-guard PreToolUse hook.

Fires on tool names matching ``mcp__mysql.*`` — every MCP server whose
NAME begins with ``mysql`` (`mysql`, `mysql-shop-prod`, …), per the
naming contract in ADOPTION.md. Own hooks.json matcher — NOT under
bin/security-dispatch.sh, whose matcher covers built-in tools only. The
credential probe targets the SPECIFIC server the call addresses, parsed
from the tool name, so each lane is graded on its own credential. Two
layers, first-deny-wins:

  1. Statement filter — write-shaped tool name or SQL input → deny.
  2. Credential probe — SHOW GRANTS beyond the read allowlist → deny;
     unverifiable → deny in halt mode (VISION §4.7 fail closed). A lane
     whose launcher resolves credentials at spawn declares a credential
     command so it is verifiable rather than permanently unverifiable
     (ADOPTION.md "Late-bound credentials"); the refusal message names
     which wall was hit (`verdict.reason`) instead of sending every
     failure to the same grant-narrowing advice.

``SPLOCK_MYSQL_MCP_GUARD``: halt (default) denies; warn logs to stderr and
allows; off skips both layers.

Refusal mechanism: JSON permissionDecision "deny" on stdout, exit 0 —
identical contract to bin/_hooks/safe_ddl_hook.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bin._env_paths import project_root
from bin._mysql_mcp_guard import probe as probe_mod
from bin._mysql_mcp_guard import statement

_FIX_HINT = (
    " Fix: narrow the mysql MCP server's DB user to SELECT/SHOW VIEW "
    "(see ADOPTION.md 'MySQL MCP for /qna and /recon'), or downgrade with "
    "SPLOCK_MYSQL_MCP_GUARD=warn (not recommended)."
)

# An `unverifiable` refusal is not a grant problem, so it must not carry the
# grant advice: the credential may well be read-only and simply unreadable
# from here. `verdict.detail` already names what is missing for this reason.
_UNVERIFIABLE_TAIL = (
    " A gate that could not run is not a pass (VISION §4.7) — this is a "
    "refusal to certify, not a finding that the credential can write."
)


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
            [str(binpath), "mysql-mcp-guard", action, message[:200]],
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
    if not tool_name.startswith("mcp__mysql"):
        _hook_log("ok", f"tool={tool_name} (out-of-scope)")
        return 0

    mode = probe_mod.resolve_mode()
    if mode == "off":
        _hook_log("ok", f"tool={tool_name} mode=off")
        return 0

    tool_input = data.get("tool_input", {})

    # Layer 1 — statement / tool-name filter (pure text, no DB roundtrip).
    reason = statement.check_tool_call(tool_name, tool_input)
    if reason:
        if mode == "warn":
            _hook_log("warned", f"tool={tool_name} {reason}")
            print(f"mysql-mcp-guard WARN: {reason}", file=sys.stderr)
        else:
            _emit_deny(f"mysql MCP write refused — {reason}.{_FIX_HINT}")
            _hook_log("blocked", f"tool={tool_name} {reason}")
            return 0

    # Layer 2 — credential probe of the server this call addresses
    # (ok-verdicts cached 15 min, keyed per server).
    server, _tool = statement.split_mcp_tool_name(tool_name)
    verdict = probe_mod.probe(project_root(), server=server or "mysql")
    if verdict.status in ("inert", "ok"):
        _hook_log("ok", f"tool={tool_name} probe={verdict.status}")
        return 0
    graded = verdict.status + (f"/{verdict.reason}" if verdict.reason else "")
    if mode == "warn":
        _hook_log("warned", f"tool={tool_name} probe={graded}")
        print(f"mysql-mcp-guard WARN: {verdict.detail}", file=sys.stderr)
        return 0
    if verdict.status == "write_capable":
        _emit_deny(
            f"mysql MCP call refused — {verdict.detail}."
            + _FIX_HINT
            + " The MySQL user must be limited to SELECT-class grants before "
            "mysql MCP calls are allowed through."
        )
    else:
        _emit_deny(
            f"mysql MCP call refused ({verdict.reason or 'unverifiable'}) — "
            f"{verdict.detail}." + _UNVERIFIABLE_TAIL
        )
    _hook_log("blocked", f"tool={tool_name} probe={graded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
