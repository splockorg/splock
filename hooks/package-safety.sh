#!/usr/bin/env bash
# .claude/hooks/package-safety.sh — PreToolUse hook on Bash install commands.
#
# Spec: plan §G.7.1 + implplan §G.impl.7. Dispatched via
# bin/security-dispatch.sh; called for tool = Bash AND command matches
# INSTALL_COMMAND_PATTERNS.
#
# Refuses package installs when:
#   - Version pin is `latest` / `*` / unbounded range
#   - No lockfile present in the repo (per package manager)
#   - First-publish-date < (now - PACKAGE_SAFETY_AGE_THRESHOLD_DAYS)
#   - Weekly downloads < PACKAGE_SAFETY_DOWNLOAD_FLOOR (when > 0)
#
# Citation requirement (REQUIRED per bin/hook-lint rule R-PACKAGE-SAFETY-CITATION):
#   research_findings_v1.md §D Spracklen et al. arXiv:2406.10279 —
#       5.2% / 21.7% commercial / open-source LLM package-hallucination rate
#   research_findings_v1.md §G real-world incidents — Aikido react-codeshift
#       campaign; npm shai-hulud Sep 2025; litellm Mar 2026
#
# Why threshold = 14 days (not 3-7): Snyk industry baseline 14-22 days;
# pnpm minimumReleaseAge default 1 day + 2-week common config. The 3-7
# day window is the rejected claim per research_findings_v1.md Appendix.
#
# Refusal mechanism: JSON permissionDecision: "deny" on stdout, exit 0.
# Defense-in-depth: settings-level deny per plan §G.7.1 closing line.
#
# Exit codes:
#   0 = always (allowed silently OR refused with JSON deny on stdout)
#
# Audit-trail emit: bin/hook-log package-safety {ok|blocked} "..."
#
# Env-var inputs (registered in §I.impl):
#   PACKAGE_SAFETY_AGE_THRESHOLD_DAYS — int, default 14, range 7-60
#   PACKAGE_SAFETY_DOWNLOAD_FLOOR — int, default 500 (0 disables)
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
printf '%s' "$HOOK_INPUT" | "$(command -v python || command -v python3)" -m bin._hooks.package_safety_hook
exit "$?"
