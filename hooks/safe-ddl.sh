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
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

VENV_PATH="${SPLOCK_VENV:-.venv}"
if [ -f "$VENV_PATH/bin/activate" ] && [ -z "${VIRTUAL_ENV:-}" ]; then
    # shellcheck disable=SC1091
    source "$VENV_PATH/bin/activate"
fi

HOOK_INPUT="$(cat || true)"

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$REPO_ROOT"
printf '%s' "$HOOK_INPUT" | python -m bin._hooks.safe_ddl_hook
exit "$?"
