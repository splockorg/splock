#!/usr/bin/env bash
# .claude/hooks/verify-on-stop.sh — Stop hook (Option B minimal).
# Per plan §G.6.2 + Anthropic issue #55754. Recursion guard via
# stop_hook_active + bin/hook-log emit + exit 0.
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# REPO_ROOT = the directory that contains bin/ . Prefer the Claude Code
# plugin root (set for plugin hooks; holds bin/); else detect whether this
# hooks dir sits one level (plugin layout) or two (embedded .claude/hooks/)
# below the dir that holds bin/_hooks. Fixes the off-by-one that broke
# `python -m bin._hooks.*` under the plugin layout (fork finding F6).
__HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -d "${CLAUDE_PLUGIN_ROOT}/bin/_hooks" ]; then
    REPO_ROOT="${CLAUDE_PLUGIN_ROOT}"
elif [ -d "$__HOOK_DIR/../bin/_hooks" ]; then
    REPO_ROOT="$(cd "$__HOOK_DIR/.." && pwd)"
else
    REPO_ROOT="$(cd "$__HOOK_DIR/../.." && pwd)"
fi

VENV_PATH="${SPLOCK_VENV:-.venv}"
if [ -f "$VENV_PATH/bin/activate" ] && [ -z "${VIRTUAL_ENV:-}" ]; then
    source "$VENV_PATH/bin/activate"
fi

HOOK_INPUT="$(cat || true)"

STOP_HOOK_ACTIVE="$(printf '%s' "$HOOK_INPUT" | python -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print("true" if d.get("stop_hook_active") else "false")
except Exception:
    print("false")
' 2>/dev/null || echo "false")"

if [ "$STOP_HOOK_ACTIVE" = "true" ]; then
    exit 0
fi

if [ -x "$REPO_ROOT/bin/hook-log" ]; then
    "$REPO_ROOT/bin/hook-log" verify-on-stop ok "Option B stub; v2.64 §9 catalog deferred per SRR.1" 2>/dev/null || true
fi

exit 0
