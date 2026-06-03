#!/usr/bin/env bash
# .claude/hooks/chain-sealed-state-delete-block.sh — PreToolUse hook
# (Bash / Edit / Write) refusing agent-side deletes of sealed-state paths.
#
# Spec: plan §G.3 + implplan §G.impl.5. Always-on; no chain dependency
# (Replit panic-cascade-class refusal applies any step, any session, any mode).
#
# Finding 2 dual-altitude defense (orchestrator §4a.5 RATIFIED 2026-05-21
# shape (a)): this hook catches AGENT delete paths via the PreToolUse
# tool-use interception layer. The driver-layer twin
# `bin/_chain_overnight/pre_stage.py` catches shell `git add` paths on
# the driver process — PreToolUse hooks structurally don't fire on the
# driver's own shell invocations, so the dual-altitude defense is
# load-bearing, not redundant.
#
# Detection:
#   - Bash: parse command for delete-shaped patterns (rm, rm -rf,
#     find ... -delete, > <p>, : > <p>, truncate -s 0 <p>, mv <p> /dev/null);
#     extract candidate paths.
#   - Edit/Write: if proposed content empty/whitespace AND target exists
#     with non-empty content → delete-equivalent.
#
# Match against .claude/hooks/sealed_paths.txt (single source of truth).
#
# Refusal: JSON permissionDecision: "deny" on stdout, exit 0.
# Defense-in-depth: settings-level deny per §G.impl.13 covers each
# sealed path on the Edit-tool gotcha #37210 path.
#
# Exit codes:
#   0 = always (allowed silently OR refused with JSON deny on stdout)
#
# Audit-trail emit: bin/hook-log chain-sealed-state-delete-block {ok|blocked} "..."
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

VENV_PATH="${SPLOCK_VENV:-.venv}"
if [ -f "$VENV_PATH/bin/activate" ] && [ -z "${VIRTUAL_ENV:-}" ]; then
    # shellcheck disable=SC1091
    source "$VENV_PATH/bin/activate"
fi

HOOK_INPUT="$(cat || true)"

# Resolve PYTHONPATH to the real repo so `bin._hooks.*` resolves, while
# keeping cwd unchanged so the Python entry can stat candidate paths
# relative to the agent's working directory.
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$REPO_ROOT"
printf '%s' "$HOOK_INPUT" | python -m bin._hooks.sealed_delete_hook
exit "$?"
