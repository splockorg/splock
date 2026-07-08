#!/usr/bin/env bash
# .claude/hooks/intent-on-first-edit.sh — PreToolUse hook
# (Edit / Write) refusing un-registered or out-of-scope agent edits.
#
# Spec: plan §P.9 + implplan §P.impl.9. Always-on; no chain dependency
# (P.impl.17 #2 RATIFIED 2026-05-21; matches §G.impl.5
# chain-sealed-state-delete-block pattern — structural enforcement
# substrate, un-registered work in any session is the failure mode).
#
# Three refusal cases (P.impl.9 lines 8849-8853):
#   1. No active register for current session AND target under another
#      session's claimed_paths → permissionDecision: deny
#   2. Session registered but target OUTSIDE its claimed_paths →
#      permissionDecision: warn (soft; controlled by
#      intent.soft_warning_path_prefix_enabled knob)
#   3. Session kind=read_only_recon|read_only_review attempting Edit/Write
#      → permissionDecision: deny
#
# Read-only contract: reads docs/intent/intent_local.jsonl only; no
# MySQL query on the hot path. Best-effort audit row via bin/hook-log.
#
# Exit codes:
#   0 = always (allowed silently OR refused with JSON deny on stdout)
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
printf '%s' "$HOOK_INPUT" | "$(command -v python || command -v python3)" -m bin._intent.hook_resolver
exit "$?"
