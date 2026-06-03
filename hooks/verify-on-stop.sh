#!/usr/bin/env bash
# .claude/hooks/verify-on-stop.sh — Stop hook (Option B minimal).
# Per plan §G.6.2 + Anthropic issue #55754. Recursion guard via
# stop_hook_active + bin/hook-log emit + exit 0.
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

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
