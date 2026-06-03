#!/usr/bin/env bash
# .claude/hooks/splock-subagent-stop.sh — SubagentStop hook (Phase C).
#
# Fires when an Agent tool invocation completes. Reads the most recently
# modified `agent-*.jsonl` under the parent session's subagents dir and
# upserts its row into `extraction.agent_subagents`.
#
# Fail-open + 5s timeout. Always exits 0.
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

VENV_PATH="${SPLOCK_VENV:-.venv}"
if [ -f "$VENV_PATH/bin/activate" ] && [ -z "${VIRTUAL_ENV:-}" ]; then
    # shellcheck disable=SC1091
    source "$VENV_PATH/bin/activate"
fi

HOOK_INPUT="$(cat || true)"
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$REPO_ROOT"

PARENT_SID="$(printf '%s' "$HOOK_INPUT" | python -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print((d.get("session_id") or "").strip())
except Exception:
    pass
' 2>/dev/null || true)"

if [ -z "$PARENT_SID" ]; then
    "$REPO_ROOT/bin/hook-log" splock-subagent-stop ok "no_session_id" >/dev/null 2>&1 || true
    exit 0
fi

TMP_STDERR="$(mktemp 2>/dev/null || echo /tmp/splock-subagent-stop-stderr.$$)"
RC=0
timeout 5 python -m bin._intent.hook_writer subagent_stop \
    --session-id "$PARENT_SID" \
    >/dev/null 2>"$TMP_STDERR" \
  || RC=$?

if [ "$RC" -ne 0 ]; then
    DETAIL="rc=$RC"
    if [ -s "$TMP_STDERR" ]; then
        DETAIL="$DETAIL stderr=$(head -c 160 "$TMP_STDERR" 2>/dev/null || true)"
    fi
    "$REPO_ROOT/bin/hook-log" splock-subagent-stop error "$DETAIL" >/dev/null 2>&1 || true
else
    "$REPO_ROOT/bin/hook-log" splock-subagent-stop ok "ok" >/dev/null 2>&1 || true
fi
rm -f "$TMP_STDERR" 2>/dev/null || true
exit 0
