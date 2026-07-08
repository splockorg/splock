#!/usr/bin/env bash
# .claude/hooks/test-at-edit.sh
#
# PostToolUse hook (matcher Edit|Write) — verify per implplan §M.impl.6.
# Settings.json wiring pre-staged at §G.impl.13 line 4265 (per spec); this
# script provides the implementation only.
#
# Verification-only contract per R-POSTTOOL-NO-DENY: always exits 0,
# never refuses. Failure surfacing is logged to
# `.claude/state/test_at_edit_log.jsonl` for the operator + reviewer
# subagent (§F.impl) to consume at the code-step boundary.
#
# Decision flow (per M.impl.6):
#   1. Read tool_input.file_path from stdin event JSON.
#   2. Skip on test/docs/sealed/non-source paths.
#   3. Discover matching test files (path-mirror + symbol-grep fallback).
#   4. Run pytest on each (per-file 60s timeout; total 60s budget).
#   5. Log per-invocation row.
#   6. Always exit 0.
#
# Audit-trail emit: bin/hook-log test-at-edit {ok|flagged} "..."
set -u  # do NOT `set -e` — PostToolUse must always exit 0

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
cd "$REPO_ROOT"

VENV_PATH="${SPLOCK_VENV:-.venv}"
if [ -f "$VENV_PATH/bin/activate" ] && [ -z "${VIRTUAL_ENV:-}" ]; then
    # shellcheck disable=SC1091
    source "$VENV_PATH/bin/activate"
fi

# Forward stdin verbatim to the Python backing.
python -m bin._hooks.test_at_edit >/dev/null 2>&1 || true

"$REPO_ROOT/bin/hook-log" test-at-edit ok "post-edit verification dispatched" >/dev/null 2>&1 || true

exit 0
