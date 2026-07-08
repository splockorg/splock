#!/usr/bin/env bash
# .claude/hooks/chain-test-file-edit-flag.sh — PostToolUse hook
# (Edit/Write) flagging test-file edits during the §F.3 test-step
# retry window.
#
# Per splock plan §F.4 + implplan §F.impl.4. This hook is
# DETECTION ONLY — it does not block. The flag content feeds R4
# (tampering check) of the constrained rubric per §F.5 / §F.impl.5.
#
# Anchor §4a.3 — runtime test-step window only. Output target is the
# `_sonnet_input_iter<N>_test_edits.jsonl` staging file consumed by
# `bin/_retry_loop/briefing.py::_render_test_step_prompt` (which
# embeds it under `## Test-file edit flag` in the deterministically-
# constructed reviewer prompt). NEVER agent-authored.
#
# Activation window:
#   SPLOCK_PLAN_SLUG set AND SPLOCK_CHAIN_ID set AND SPLOCK_PHASE == 5
#   AND SPLOCK_ITERATION_N set (passed by the chain driver to scope the
#   staging file per iteration).
#
# Output:
#   Append one JSON line to `<plan_dir>/_sonnet_input_iter<N>_test_edits.jsonl`:
#     {"path": "<file>", "diff": "<short diff>", "ts": "<iso>"}
#
# Exit codes:
#   0 = always (detection-only; never blocks)

set -euo pipefail

# Activation guard — runtime test-step window only.
SPLOCK_PLAN_SLUG="${SPLOCK_PLAN_SLUG:-}"
SPLOCK_CHAIN_ID="${SPLOCK_CHAIN_ID:-}"
SPLOCK_PHASE="${SPLOCK_PHASE:-}"
SPLOCK_ITERATION_N="${SPLOCK_ITERATION_N:-}"

if [ -z "$SPLOCK_PLAN_SLUG" ] || [ -z "$SPLOCK_CHAIN_ID" ] || [ "$SPLOCK_PHASE" != "5" ]; then
  exit 0
fi

if [ -z "$SPLOCK_ITERATION_N" ]; then
  # Defensive: missing iteration scope means the staging file path is
  # ambiguous. Skip rather than write to a unscoped file.
  exit 0
fi

# Read the PostToolUse hook input (Claude Code passes JSON on stdin).
HOOK_INPUT="$(cat || true)"

# Delegate test-file classification to the Python helper so the
# canonical pattern set lives in one place
# (`bin/_retry_loop/reversibility.py::is_test_file()`).
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
VENV="${SPLOCK_VENV:-.venv}/bin/activate"
if [ -z "${VIRTUAL_ENV:-}" ] && [ -f "$VENV" ]; then
  # shellcheck disable=SC1090
  source "$VENV"
fi

PYTHON_RESULT="$(
  cd "$REPO_ROOT" && SPLOCK_PLAN_SLUG="$SPLOCK_PLAN_SLUG" SPLOCK_ITERATION_N="$SPLOCK_ITERATION_N" \
  python -c '
import datetime
import json
import os
import sys
from pathlib import Path

raw = sys.stdin.read()
try:
    data = json.loads(raw) if raw.strip() else {}
except (json.JSONDecodeError, ValueError):
    data = {}

tool_input = data.get("tool_input", {}) if isinstance(data, dict) else {}
file_path = ""
diff_excerpt = ""
if isinstance(tool_input, dict):
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    # Best-effort diff capture from the hook input.
    diff_excerpt = (
        tool_input.get("new_string", "")
        or tool_input.get("content", "")
    )[:512]

from bin._retry_loop.reversibility import is_test_file

if not file_path or not is_test_file(file_path):
    print(json.dumps({"flagged": False}))
    sys.exit(0)

# Resolve plan directory + staging file.
slug = os.environ["SPLOCK_PLAN_SLUG"]
iter_n = os.environ["SPLOCK_ITERATION_N"]
plan_dir = Path("docs") / "plans" / slug
staging = plan_dir / f"_sonnet_input_iter{iter_n}_test_edits.jsonl"
staging.parent.mkdir(parents=True, exist_ok=True)

entry = {
    "path": file_path,
    "diff": diff_excerpt,
    "ts": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
}
with staging.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(entry) + "\n")
print(json.dumps({"flagged": True, "staging": str(staging)}))
' <<< "$HOOK_INPUT"
)" || true

# Always exit 0 — this hook is detection-only.
exit 0
