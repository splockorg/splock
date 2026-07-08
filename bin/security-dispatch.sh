#!/usr/bin/env bash
# bin/security-dispatch.sh — umbrella PreToolUse dispatcher.
#
# Spec: plan §G.5 + implplan §G.impl.10. Consolidates the four
# security-tier checks (`sealed-paths`, `package-safety`, `safe-ddl`,
# `guardrail-spawn`) behind a single PreToolUse entry per
# plan §9.A "Hooks must be independent" / consolidate ordered logic.
#
# Dispatch order (deterministic, first-deny-wins):
#   1. sealed-paths.sh                — tool ∈ {Edit, Write, Read}
#   2. package-safety.sh              — tool = Bash AND install-command shape
#   3. safe-ddl.sh                    — tool = Bash AND DDL-command shape
#   4. .claude/hooks/guardrail-spawn.sh — tool = Task (if installed)
#
# State/behavioral hooks NOT under dispatch (own settings.json entries):
#   - splock-session-start  (SessionStart)
#   - chain-suppression-block  (§F.impl PreToolUse)
#   - chain-sealed-state-delete-block  (PreToolUse Bash|Edit|Write)
#   - chain-test-file-edit-flag  (§F.impl PostToolUse)
#   - intent-on-first-edit  (§P.impl PreToolUse)
#   - lazy-dump-cap  (§C.impl / §3.C PreToolUse)
#   - test-at-edit  (§M.impl PostToolUse)
#   - verify-on-stop  (§N.impl Stop)
#
# Short-circuit semantics: first sub-hook to emit JSON deny wins;
# dispatcher echoes its stdout, exits 0. If all sub-hooks exit 0
# silently, dispatcher exits 0 silently.
#
# Exit codes:
#   0 = always (per PreToolUse JSON-deny contract; refusal is on stdout)
#
# Audit-trail emit: bin/hook-log security-dispatch ok "..." (per
# routed sub-hook + dispatcher self-trace).
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

VENV_PATH="${SPLOCK_VENV:-.venv}"
if [ -f "$VENV_PATH/bin/activate" ] && [ -z "${VIRTUAL_ENV:-}" ]; then
    # shellcheck disable=SC1091
    source "$VENV_PATH/bin/activate"
fi

HOOK_INPUT="$(cat || true)"

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$REPO_ROOT"
printf '%s' "$HOOK_INPUT" | "$(command -v python || command -v python3)" -m bin._hooks.security_dispatch
exit "$?"
