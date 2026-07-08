#!/usr/bin/env bash
# bin/plan-render-on-edit.sh — PostToolUse hook (Edit|Write).
#
# After a draft <slug>_plan.json edit, validate + re-render the plan via
# bin/_hooks.plan_render_on_edit (runs `bin/render_plan <slug> --kind plan`,
# which validates against plan_v1.schema.json and refreshes the .md twin).
# Non-plan-json edits are a silent skip. Always exits 0 (PostToolUse cannot
# block). Mirrors the bin/security-dispatch.sh wrapper convention.
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
printf '%s' "$HOOK_INPUT" | "$(command -v python || command -v python3)" -m bin._hooks.plan_render_on_edit
exit "$?"
