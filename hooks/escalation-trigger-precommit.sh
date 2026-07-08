#!/usr/bin/env bash
# .claude/hooks/escalation-trigger-precommit.sh
#
# PreToolUse hook (matcher Edit|Write) — enforce closed-set escalation
# triggers per implplan §L.impl.4. Net-new at v1.3 ship; registered in
# `.claude/settings.json` PreToolUse array alongside `lazy-dump-cap`.
#
# Despite the `-precommit` filename suffix (mnemonic for the Git-precommit
# conceptual analog), this hook fires at PreToolUse — the only event slot
# the Claude Code SDK exposes for tool-use interception.
#
# Behavior:
#   1. Invoke `bin/route_issue --check-scope`.
#   2. Propagate its exit code: 0 (clean) or 25 (trigger fired).
#
# Triggers detected:
#   - blast_radius   (> ESCALATION_BLAST_RADIUS_FILES staged files)
#   - cross_repo     (file outside repo root via abs path / symlink escape)
#   - cross_vertical (>1 process_graph.yaml vertical touched)
#
# Audit-trail emit: bin/hook-log escalation-trigger-precommit {ok|blocked} "..."
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
cd "$REPO_ROOT"

VENV_PATH="${SPLOCK_VENV:-.venv}"
if [ -f "$VENV_PATH/bin/activate" ] && [ -z "${VIRTUAL_ENV:-}" ]; then
    # shellcheck disable=SC1091
    source "$VENV_PATH/bin/activate"
fi

if "$REPO_ROOT/bin/route_issue" --check-scope; then
    "$REPO_ROOT/bin/hook-log" escalation-trigger-precommit ok "scope clean" >/dev/null 2>&1 || true
    exit 0
else
    rc=$?
    "$REPO_ROOT/bin/hook-log" escalation-trigger-precommit blocked "trigger fired exit=$rc" >/dev/null 2>&1 || true
    exit "$rc"
fi
