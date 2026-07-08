#!/usr/bin/env bash
# .claude/hooks/eval-gate-pre-commit.sh — PreToolUse hook for git commit
# (splock §J.impl.9).
#
# Fired before any commit operation. Reads staged files; dispatches
# `bin/eval-gate` per the interactive/chain matrix:
#   * SPLOCK_CHAIN_ID unset + touch-path match → strict mode (exit 32 refuses)
#   * SPLOCK_CHAIN_ID set + touch-path match → report-only (always permits)
#   * No touch-path → exit 0 silently
#
# Escape hatch: EVAL_GATE_OVERRIDE=1 permits commit on regression
# (interactive branch only); loud-logged to _orchestrator_log.jsonl with
# `override_in_effect.operator_override: true` + caller's
# EVAL_GATE_OVERRIDE_REASON or "override unspecified".
set -euo pipefail

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

exec "$(command -v python || command -v python3)" -m bin._eval_gate.main --from-precommit-hook "$@"
