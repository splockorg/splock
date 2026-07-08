#!/usr/bin/env bash
# .claude/hooks/lazy-dump-cap.sh
#
# PreToolUse hook (matcher Edit|Write) — enforce the lazy-dump cap per
# implplan §L.impl.7. Pre-registered in `.claude/settings.json` at
# §G.impl.13 line 3840 (this script provides the implementation only).
#
# Behavior:
#   1. Detect whether the staged diff touches `docs/outstanding_issues.md`.
#      If not, exit 0 silently (nothing to enforce).
#   2. Otherwise, invoke `bin/lazy-dump-check --pre-commit`.
#   3. Propagate its exit code: 0 (clean) or 26 (cap exceeded).
#
# Despite the `lazy-dump-cap` name, the hook fires on PreToolUse rather
# than PreCommit (Claude Code SDK does not expose PreCommit). This is
# §L.impl.1 status-table NIT correction (v1.3-revised).
#
# Audit-trail emit: bin/hook-log lazy-dump-cap {ok|blocked} "..."
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
cd "$REPO_ROOT"

VENV_PATH="${SPLOCK_VENV:-.venv}"
if [ -f "$VENV_PATH/bin/activate" ] && [ -z "${VIRTUAL_ENV:-}" ]; then
    # shellcheck disable=SC1091
    source "$VENV_PATH/bin/activate"
fi

# Quick gate: is the outstanding-issues file in the staged set?
STAGED=$(git diff --cached --name-only 2>/dev/null || true)
MATCH=0
for path in $STAGED; do
    if [ "$path" = "docs/outstanding_issues.md" ]; then
        MATCH=1
        break
    fi
done

if [ "$MATCH" -eq 0 ]; then
    # Idempotent — no relevant staged change
    "$REPO_ROOT/bin/hook-log" lazy-dump-cap ok "no staged outstanding_issues.md" >/dev/null 2>&1 || true
    exit 0
fi

if "$REPO_ROOT/bin/lazy-dump-check" --pre-commit; then
    "$REPO_ROOT/bin/hook-log" lazy-dump-cap ok "session within cap" >/dev/null 2>&1 || true
    exit 0
else
    rc=$?
    "$REPO_ROOT/bin/hook-log" lazy-dump-cap blocked "lazy-dump-cap exit=$rc" >/dev/null 2>&1 || true
    exit "$rc"
fi
