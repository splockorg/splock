#!/usr/bin/env bash
# .claude/hooks/splock-user-prompt-submit.sh — UserPromptSubmit hook.
#
# Two legs:
#   (Part C) bin/intent register --upsert — idempotent registration +
#     last_activity_at bump for sessions missing from agent_sessions.
#   (Phase B) python -m bin._intent.hook_writer user_prompt — populate
#     the cross-machine columns (custom_title, git_branch, workflow_stage,
#     recent_prompts, tools_used_count, files_touched, todo_state,
#     last_user_prompt_at, live_status=busy).
#
# Both legs are fail-open + timed-out so a hook failure never blocks the
# prompt. Always exits 0.
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

CLAUDE_SESSION_ID="$(printf '%s' "$HOOK_INPUT" | "$(command -v python || command -v python3)" -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print((d.get("session_id") or "").strip())
except Exception:
    pass
' 2>/dev/null || true)"

if [ -z "$CLAUDE_SESSION_ID" ]; then
    "$REPO_ROOT/bin/hook-log" splock-user-prompt-submit ok "no_session_id" >/dev/null 2>&1 || true
    exit 0
fi

# Leg 1: Part C upsert (idempotent register).
INTENT_BIN="$REPO_ROOT/bin/intent"
AREA="${SPLOCK_INTENT_AREA:-unscoped_interactive}"
TMP_STDERR="$(mktemp 2>/dev/null || echo /tmp/splock-user-prompt-submit-stderr.$$)"
REGISTER_RC=0
if [ -x "$INTENT_BIN" ]; then
    SPLOCK_INTENT_COLLISION_HALT_ACTION=log_only \
    SPLOCK_INTENT_AUTO_REGISTER_INTERACTIVE=1 \
        timeout 5 "$INTENT_BIN" register \
            --upsert \
            --area "$AREA" \
            --paths "_unscoped" \
            --kind interactive \
            --closure "session_timeout:240m" \
            --emitted-by user_prompt_submit_auto \
            --claude-session-id "$CLAUDE_SESSION_ID" \
            --json \
            >/dev/null 2>"$TMP_STDERR" \
        || REGISTER_RC=$?
fi

# Leg 2: Phase B writer (populates cross-machine columns).
WRITER_RC=0
timeout 5 "$(command -v python || command -v python3)" -m bin._intent.hook_writer user_prompt \
    --session-id "$CLAUDE_SESSION_ID" \
    >/dev/null 2>>"$TMP_STDERR" \
  || WRITER_RC=$?

# Aggregate exit + log.
TOTAL_RC=$((REGISTER_RC + WRITER_RC))
if [ "$TOTAL_RC" -ne 0 ]; then
    DETAIL="register=$REGISTER_RC writer=$WRITER_RC"
    if [ -s "$TMP_STDERR" ]; then
        DETAIL="$DETAIL stderr=$(head -c 160 "$TMP_STDERR" 2>/dev/null || true)"
    fi
    "$REPO_ROOT/bin/hook-log" splock-user-prompt-submit error "$DETAIL" >/dev/null 2>&1 || true
else
    "$REPO_ROOT/bin/hook-log" splock-user-prompt-submit ok "ok" >/dev/null 2>&1 || true
fi

rm -f "$TMP_STDERR" 2>/dev/null || true
exit 0
