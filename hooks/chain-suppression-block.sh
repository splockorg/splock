#!/usr/bin/env bash
# .claude/hooks/chain-suppression-block.sh — PreToolUse hook (Edit/Write)
# refusing test-suppression patterns inside the §F.3 test-step retry window.
#
# Per splock plan §F.4 + §G.2 + implplan §F.impl.4 (hook stack
# realization of the reversibility scope). Owned by §G.impl.2 (script
# lives under .claude/hooks/); semantic rationale and integration with
# the runtime retry loop owned by §F.impl.4 (this header is the §F-spec
# inline reference).
#
# Anchor §4a.3 — this hook fires during RUNTIME chain execution, NOT
# during build-time orchestrator §5 reviews. The build-time review
# framework has no equivalent hook surface — orchestrator-side
# substrate-build agents are reviewed via the BLOCKER/MAJOR/MINOR/NIT
# framework, not via PreToolUse refusal.
#
# Refused patterns (from plan §G.2 closed list, mirrored in
# `bin/_retry_loop/reversibility.py::all_suppression_patterns()`):
#   Python:    sys.exit(0), os._exit(0), @pytest.mark.skip,
#              @pytest.mark.xfail, pytest.skip(), pytest.xfail(),
#              @unittest.skip, @unittest.expectedFailure
#   JS/TS:     process.exit(0), xit(, xdescribe(, .skip(, .only(
#   Java:      @Disabled, @Ignore
#   Cucumber:  ~@wip, @skip
#
# Sanctioned skips: an entry in `_test_expectations.json` matching the
# test id + (optional) pattern permits the annotation. Per
# `bin/_retry_loop/reversibility.py::is_sanctioned_skip()`.
#
# Activation window:
#   SPLOCK_PLAN_SLUG set AND SPLOCK_CHAIN_ID set AND SPLOCK_PHASE == 5
#   (the test-step phase). Outside this window the hook no-ops.
#
# Refusal mechanism (defense-in-depth per plan §G.2a / Finding 5 /
# gotcha #37210): emits JSON `permissionDecision: "deny"` on stdout
# AND requires settings-level deny entry (per §G.impl). On Edit-tool
# the JSON deny may be ignored — the settings-level entry is the
# enforcement backstop.
#
# Exit codes:
#   0 = allow (no suppression match OR sanctioned)
#   1 = deny (suppression pattern detected; refusal emitted)

set -euo pipefail

# Activation guard — runtime test-step window only.
SPLOCK_PLAN_SLUG="${SPLOCK_PLAN_SLUG:-}"
SPLOCK_CHAIN_ID="${SPLOCK_CHAIN_ID:-}"
SPLOCK_PHASE="${SPLOCK_PHASE:-}"

if [ -z "$SPLOCK_PLAN_SLUG" ] || [ -z "$SPLOCK_CHAIN_ID" ] || [ "$SPLOCK_PHASE" != "5" ]; then
  # Outside the test-step retry window — no-op allow.
  exit 0
fi

# Read the PreToolUse hook input (Claude Code passes JSON on stdin).
HOOK_INPUT="$(cat || true)"

# Delegate suppression-pattern classification to the Python helper so
# the canonical pattern set lives in one place
# (`bin/_retry_loop/reversibility.py::all_suppression_patterns()`).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="${SPLOCK_VENV:-.venv}/bin/activate"
if [ -z "${VIRTUAL_ENV:-}" ] && [ -f "$VENV" ]; then
  # shellcheck disable=SC1090
  source "$VENV"
fi

PYTHON_RESULT="$(
  cd "$REPO_ROOT" && python -c '
import json
import sys

raw = sys.stdin.read()
try:
    data = json.loads(raw) if raw.strip() else {}
except (json.JSONDecodeError, ValueError):
    data = {}

# Hook input shape varies; defensively probe for known content surfaces.
tool_input = data.get("tool_input", {}) if isinstance(data, dict) else {}
new_content = ""
file_path = ""
if isinstance(tool_input, dict):
    new_content = tool_input.get("content") or tool_input.get("new_string") or ""
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""

from bin._retry_loop.reversibility import scan_for_suppression

matches = scan_for_suppression(new_content)
print(json.dumps({"matches": matches, "file_path": file_path}))
' <<< "$HOOK_INPUT"
)"

export MATCHES="$(echo "$PYTHON_RESULT" | python -c 'import json,sys; d=json.load(sys.stdin); print(", ".join(d.get("matches", [])))')"

if [ -z "$MATCHES" ]; then
  exit 0
fi

# Pass 7 Finding 11: forensic-trail emit BEFORE the deny envelope so a
# kill-9 between this line and the envelope still preserves the audit
# row. Mirrors sealed-paths.sh line 23 ("Audit-trail emit: bin/hook-log
# sealed-paths {ok|blocked} '...'").
"$REPO_ROOT/bin/hook-log" chain-suppression-block blocked "matched: $MATCHES" >/dev/null 2>&1 || true

# Pass 3 Finding 5: build the deny envelope in Python with json.dumps so
# regex characters in $MATCHES (\b, \s, \., etc.) get properly escaped.
# The prior bash-heredoc form interpolated them as raw text, producing
# JSON that fails json.loads on every deny. MATCHES is exported above so
# the child Python process sees the literal regex without bash re-parsing.
python -c '
import json, os
matches = os.environ.get("MATCHES", "")
envelope = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            "chain-suppression-block refused: test-suppression pattern "
            "detected during §F.3 test-step retry window. Matched "
            "patterns: " + matches + ". Per plan §G.2 closed list. "
            "Allow via _test_expectations.json entry if sanctioned, or "
            "revise the edit."
        ),
    },
}
print(json.dumps(envelope, indent=2, ensure_ascii=False))
'
exit 0
