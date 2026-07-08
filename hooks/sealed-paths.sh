#!/usr/bin/env bash
# .claude/hooks/sealed-paths.sh — PreToolUse umbrella on Read / Edit / Write.
#
# Spec: implplan §G.impl.9 + plan §G.7.2 (user-home secret paths) +
# plan §G.7.3 (content-check for enableAllProjectMcpServers true).
# Dispatched via bin/security-dispatch.sh; called for tool ∈
# {Read, Edit, Write}.
#
# Always-on; no chain dependency. Refuses Read / Edit / Write on:
#   - Sealed-state paths per docs/plans/<slug>/* (cross-cutting inventory)
#   - User-home credential paths (~/.aws/**, ~/.ssh/**, ~/.docker/config.json,
#     ~/.kube/config, ~/.netrc, ~/.git-credentials)
#   - Content check on .claude/settings.json: refuses any Edit/Write that
#     introduces "enableAllProjectMcpServers": true (CVE-2025-59536)
#
# Refusal mechanism: JSON permissionDecision: "deny" on stdout, exit 0.
# Defense-in-depth: settings-level deny entries cover the path set on
# the Edit-tool gotcha #37210 path per plan §G.2a / §G.impl.13.
#
# Exit codes:
#   0 = always (allowed silently OR refused with JSON deny on stdout)
#
# Audit-trail emit: bin/hook-log sealed-paths {ok|blocked} "..."
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

# Resolve PYTHONPATH to the real repo so `bin._hooks.*` resolves,
# while keeping cwd unchanged so the Python entry interprets paths
# relative to the agent's working directory.
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$REPO_ROOT"
printf '%s' "$HOOK_INPUT" | "$(command -v python || command -v python3)" -m bin._hooks.sealed_paths_hook
EXIT="$?"
exit "$EXIT"
