#!/usr/bin/env bash
# hooks/mysql-mcp-guard.sh — PreToolUse hook on every MCP server whose
# name begins with `mysql` (`mysql`, `mysql-<site>-prod`, … — the naming
# contract per ADOPTION.md "MySQL MCP for /qna and /recon").
#
# Own hooks.json matcher ("mcp__mysql.*") — NOT under
# bin/security-dispatch.sh, whose matcher covers built-in tools only.
# Dispatches to bin/_hooks/mysql_mcp_guard_hook.py:
#
#   Layer 1: statement filter — write-shaped tool name / SQL input → deny.
#   Layer 2: credential probe of the SPECIFIC server the call targets —
#            SHOW GRANTS beyond the read allowlist → deny; unverifiable
#            → deny in halt mode (fail closed).
#
# Mode knob: SPLOCK_MYSQL_MCP_GUARD = halt (default) | warn | off.
#
# Refusal mechanism: JSON permissionDecision: "deny" on stdout, exit 0.
#
# Exit codes:
#   0 = always (allowed silently OR refused with JSON deny on stdout)
#
# Audit-trail emit: bin/hook-log mysql-mcp-guard {ok|warned|blocked} "..."
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
printf '%s' "$HOOK_INPUT" | "$(command -v python || command -v python3)" -m bin._hooks.mysql_mcp_guard_hook
exit "$?"
