#!/usr/bin/env bash
# .claude/hooks/claude-md-discipline.sh
#
# Pre-commit hook (M.impl.3). Invoked from `.git/hooks/pre-commit` (installed
# by `bin/install-precommit-hooks` per §N.impl). NOT a Claude Code SDK
# hook — pre-commit gate only.
#
# Decision flow (per M.impl.3):
#   1. Enumerate staged CLAUDE.md paths via git diff --cached --name-only.
#   2. If empty → exit 0 silently.
#   3. For each path: check hard ceiling / soft target / LLM-emission
#      signature / auto-regenerate attempt.
#   4. Refusals emitted as JSON to stderr; non-zero exit refuses commit.
#   5. `[force-claude-md]` token in commit message downgrades refusals to
#      warnings + emits forensic log row via bin/hook-log.
#
# Audit-trail emit: bin/hook-log claude-md-discipline {ok|blocked|flagged} "..."
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

VENV_PATH="${SPLOCK_VENV:-.venv}"
if [ -f "$VENV_PATH/bin/activate" ] && [ -z "${VIRTUAL_ENV:-}" ]; then
    # shellcheck disable=SC1091
    source "$VENV_PATH/bin/activate"
fi

# Quick gate: is any CLAUDE.md path in the staged set?
STAGED=$(git diff --cached --name-only 2>/dev/null || true)
HAVE_CLAUDE_MD=0
for path in $STAGED; do
    base=$(basename "$path")
    if [ "$base" = "CLAUDE.md" ]; then
        HAVE_CLAUDE_MD=1
        break
    fi
done

if [ "$HAVE_CLAUDE_MD" -eq 0 ]; then
    "$REPO_ROOT/bin/hook-log" claude-md-discipline ok "no staged CLAUDE.md" >/dev/null 2>&1 || true
    exit 0
fi

# Dispatch to Python backing.
if python -m bin._hooks.claude_md_discipline; then
    "$REPO_ROOT/bin/hook-log" claude-md-discipline ok "all staged CLAUDE.md clean" >/dev/null 2>&1 || true
    exit 0
else
    rc=$?
    "$REPO_ROOT/bin/hook-log" claude-md-discipline blocked "claude-md-discipline exit=$rc" >/dev/null 2>&1 || true
    exit "$rc"
fi
