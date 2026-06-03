#!/usr/bin/env bash
# .claude/hooks/marker-validate-pre-commit.sh
#
# Pre-commit dispatch hook for the marker substrate (implplan §K.impl.8).
# Invoked via the v2.7 hook-dispatcher (§G.5 `bin/security-dispatch.sh`)
# when scheduled_markers/list.md OR scheduled_markers/*.md appears in the
# staged diff. Non-zero exit refuses the commit.
#
# Idempotent on no-change: when `bin/marker validate --changed-only` finds
# no staged scheduled_markers paths, it exits 0 silently.
#
# Behavior:
#   1. Read `git diff --cached --name-only`
#   2. If any path matches docs/plans/scheduled_markers/{list,*}.md, run
#      `bin/marker validate --changed-only`.
#   3. Propagate the exit code (0 / 11 / 12 / 13 / 14 / 15 per §K.impl.8).
#
# To install (one-time per clone): wire this script into the v2.7
# hook-dispatcher per §G.5. The hook is NOT auto-installed by this
# script's own existence.
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

# Detect staged scheduled_markers paths
STAGED=$(git diff --cached --name-only 2>/dev/null || true)
MATCH=0
for path in $STAGED; do
    case "$path" in
        docs/plans/scheduled_markers/list.md|docs/plans/scheduled_markers/*.md)
            MATCH=1
            break
            ;;
    esac
done

if [ "$MATCH" -eq 0 ]; then
    # Idempotent — no staged marker paths
    exit 0
fi

# Run validate against staged diff
exec "$REPO_ROOT/bin/marker" validate --changed-only
