#!/usr/bin/env bash
# .claude/hooks/safe-ddl.sh — PreToolUse hook on Bash DDL commands.
#
# Spec: plan §G.7.4 + implplan §G.impl.8. Dispatched via
# bin/security-dispatch.sh; called for tool = Bash AND command matches
# DDL_COMMAND_PATTERNS.
#
# Refuses raw DDL outside the Python DAL:
#   - mysql -e "ALTER ..." / -e "CREATE ..." / -e "DROP ..."
#   - psql -c "..." with DDL keywords
#   - mysql < migration.sql / psql -f migration.sql + content scan
#     (RATIFIED 2026-05-20: content scan of .sql files, not inline-only)
#
# Why DDL through Python DAL: per CLAUDE.md gotcha — enum-extending DDL
# needs paired cache invalidation (_ENUM_CACHE); raw DDL bypasses this.
# v2.7 §5.G "schema-change one-and-done" formalizes the discipline.
#
# No operator override per plan §G.7.4; ad-hoc DDL goes via the DAL from
# a Python REPL, not by bypassing the hook.
#
# Refusal mechanism: JSON permissionDecision: "deny" on stdout, exit 0.
#
# Exit codes:
#   0 = always (allowed silently OR refused with JSON deny on stdout)
#
# Audit-trail emit: bin/hook-log safe-ddl {ok|blocked} "..."
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
printf '%s' "$HOOK_INPUT" | python -m bin._hooks.safe_ddl_hook
exit "$?"
