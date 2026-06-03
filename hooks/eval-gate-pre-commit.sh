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
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

VENV_PATH="${SPLOCK_VENV:-.venv}"
if [ -f "$VENV_PATH/bin/activate" ] && [ -z "${VIRTUAL_ENV:-}" ]; then
    # shellcheck disable=SC1091
    source "$VENV_PATH/bin/activate"
fi

exec python -m bin._eval_gate.main --from-precommit-hook "$@"
