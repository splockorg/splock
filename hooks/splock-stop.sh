#!/usr/bin/env bash
# .claude/hooks/splock-stop.sh — Stop hook (Phase B).
#
# Fires when the agent completes a turn. Snapshots the turn's tool /
# file activity into the agent_sessions row's Phase B columns so the
# console sees up-to-date counts cross-machine.
#
# Fail-open + 5s timeout. Always exits 0.
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
    # shellcheck disable=SC1091
    source "$VENV_PATH/bin/activate"
fi

HOOK_INPUT="$(cat || true)"
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$REPO_ROOT"

CLAUDE_SESSION_ID="$(printf '%s' "$HOOK_INPUT" | python -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print((d.get("session_id") or "").strip())
except Exception:
    pass
' 2>/dev/null || true)"

if [ -z "$CLAUDE_SESSION_ID" ]; then
    "$REPO_ROOT/bin/hook-log" splock-stop ok "no_session_id" >/dev/null 2>&1 || true
    exit 0
fi

TMP_STDERR="$(mktemp 2>/dev/null || echo /tmp/splock-stop-stderr.$$)"
WRITER_RC=0
timeout 5 python -m bin._intent.hook_writer stop \
    --session-id "$CLAUDE_SESSION_ID" \
    >/dev/null 2>"$TMP_STDERR" \
  || WRITER_RC=$?

if [ "$WRITER_RC" -ne 0 ]; then
    DETAIL="writer=$WRITER_RC"
    if [ -s "$TMP_STDERR" ]; then
        DETAIL="$DETAIL stderr=$(head -c 160 "$TMP_STDERR" 2>/dev/null || true)"
    fi
    "$REPO_ROOT/bin/hook-log" splock-stop error "$DETAIL" >/dev/null 2>&1 || true
else
    "$REPO_ROOT/bin/hook-log" splock-stop ok "ok" >/dev/null 2>&1 || true
fi

rm -f "$TMP_STDERR" 2>/dev/null || true
exit 0
