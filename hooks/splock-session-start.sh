#!/usr/bin/env bash
# .claude/hooks/splock-session-start.sh — SessionStart hook.
#
# Spec: plan §G.1 + implplan §G.impl.3 + intent_session_auto_register
# plan §G.1 extension (T3 + T5).
#
# Three responsibilities (combined):
#   1. Chain-context phase manifest append (existing behavior — delegated
#      to `python -m bin._hooks.session_start_hook`).
#   2. T3 — interactive auto-register: when no chain-driver session is
#      already attributed, subprocess `bin/intent register` so the
#      session lands in `/agents/` UI + collision detection covers it.
#   3. T5 — lazy doctor trigger: after auto-register returns, fire
#      `bin/_intent.doctor_trigger.trigger_background()` so an
#      operator-activity-driven doctor sweep runs if
#      `~/.intent_doctor_last_run` is stale by
#      `intent.doctor_min_interval_minutes` (default 60). Background-
#      forked; sub-50ms happy-path overhead.
#
# Decision flow for the auto-register leg (T3):
#   - Read shell envelope (stdout of the Python entry) — yields
#     `claude_session_id` from the SessionStart hook JSON envelope.
#   - Dedup gate (recon §6.8 — research Case A / B / C):
#       Case A — BOTH SPLOCK_INTENT_SESSION_ID + SPLOCK_CHAIN_ID set → SKIP
#         (chain-overnight already auto-registered; the firing inside a
#         chain context is redundant).
#       Case B — only SPLOCK_CHAIN_ID set, no SPLOCK_INTENT_SESSION_ID →
#         REGISTER (orphan chain context; rare).
#       Case C — neither set → REGISTER (vanilla interactive session).
#   - If REGISTER: subprocess `bin/intent register` with
#       * --kind interactive
#       * --area <SPLOCK_INTENT_AREA or sentinel `unscoped_interactive`>
#       * --paths "_unscoped" (placeholder — operator narrows via update)
#       * --emitted-by session_start_auto
#       * --claude-session-id <from envelope>
#       * --closure session_timeout:240m (matches intent.ttl_minutes
#         default per research Decision 2)
#       * --json
#     under env-overrides:
#       * SPLOCK_INTENT_COLLISION_HALT_ACTION=log_only  (recon §6.11(b))
#       * SPLOCK_INTENT_AUTO_REGISTER_INTERACTIVE=1     (sub-1s budget)
#     wrapped by `timeout 5` (5s backstop) and `|| true` (fail-open).
#
# Latency-critical: the SessionStart hook runs on every `claude`
# invocation. Sub-1s end-to-end budget — the env-var fast-path is the
# enabler (skips MySQL on the cold cache).
#
# Always exits 0 — SessionStart is not permission-gating per plan §G.1.
# Failures (case 1–5 of `test_std_session_start_failopen`):
#   - bin/intent removed → REGISTER_RC=127 → hook-log error → exit 0
#   - venv missing → bin/intent's POSIX wrapper fails → || true swallows
#   - subprocess timeout → `timeout 5` returns 124 → hook-log error → exit 0
#   - MySQL unreachable → fast-path skips the leg anyway; sync_pending
#   - flock contention → 5s timeout; fail-open
# Each failure case emits `bin/hook-log splock-session-start error "..."`.
#
# SessionStart recursion (Finding 24): fires once per session start,
# NOT recursively. No stop_hook_active-style guard; hook-lint does not
# require the Stop-hook rule on SessionStart.
#
# `/clear` recovery (plan §G.1 / Finding 11): the three on-disk
# surfaces (_state.json, _chain_sessions.json, _orchestrator_log.jsonl)
# are the v2.7 recovery substrate. Deferred per-chain
# _chain_handoff.md tracked by SRR.1 marker.
#
# Exit codes:
#   0 = always
#
# Audit-trail emit: bin/hook-log splock-session-start {ok|error} "..."
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

VENV_PATH="${SPLOCK_VENV:-.venv}"
if [ -f "$VENV_PATH/bin/activate" ] && [ -z "${VIRTUAL_ENV:-}" ]; then
    # shellcheck disable=SC1091
    source "$VENV_PATH/bin/activate"
fi

HOOK_INPUT="$(cat || true)"

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$REPO_ROOT"

# Phase 1: chain-manifest leg (existing behavior) + shell-envelope emit.
# The Python entry writes ONE line of JSON to stdout shaped
# `{"claude_session_id": "...", "source": "..."}` after handling the
# manifest leg. We capture it for the auto-register leg below.
#
# SPLOCK_SESSION_START_SHELL_ENVELOPE=1 gates the envelope emission in the
# Python entry (backward-compat shim — the pre-T3 shell hook piped
# Python stdout straight through, so existing "silent" tests would
# break if envelope-emit fired unconditionally).
export SPLOCK_SESSION_START_SHELL_ENVELOPE=1
HOOK_OUTPUT="$(printf '%s' "$HOOK_INPUT" | python -m bin._hooks.session_start_hook || true)"
unset SPLOCK_SESSION_START_SHELL_ENVELOPE

# Phase 2 (T3 — intent_session_auto_register): auto-register dedup +
# subprocess to bin/intent register.
#
# Dedup matrix:
#   Case A: SPLOCK_INTENT_SESSION_ID + SPLOCK_CHAIN_ID both set → skip
#   Case B: only SPLOCK_CHAIN_ID set → register (orphan chain context)
#   Case C: neither set → register (vanilla interactive)
SPLOCK_INTENT_SESSION_ID_VAL="${SPLOCK_INTENT_SESSION_ID:-}"
SPLOCK_CHAIN_ID_VAL="${SPLOCK_CHAIN_ID:-}"

if [ -n "$SPLOCK_INTENT_SESSION_ID_VAL" ] && [ -n "$SPLOCK_CHAIN_ID_VAL" ]; then
    # Case A — skip dedup; chain-driver already auto-registered.
    "$REPO_ROOT/bin/hook-log" splock-session-start ok "dedup-skip: chain-driver auto-registered" >/dev/null 2>&1 || true
    # T5 — still fire the doctor trigger even on dedup-skip; the
    # SessionStart event itself is operator activity, and the
    # rate-limit gate keeps the doctor from over-firing.
    timeout 5 python -c "import sys; sys.path.insert(0, '$REPO_ROOT'); from bin._intent import doctor_trigger; doctor_trigger.trigger_background()" >/dev/null 2>&1 || true
    exit 0
fi

# Cases B + C — proceed to auto-register.

# Recover claude_session_id from the Python entry's stdout envelope.
# Default to empty string when absent / parse-fails (auto-register
# tolerates NULL on the column).
CLAUDE_SESSION_ID=""
if [ -n "$HOOK_OUTPUT" ]; then
    PARSED="$(printf '%s' "$HOOK_OUTPUT" \
        | python -c 'import json,sys
try:
    for line in sys.stdin.read().splitlines():
        line=line.strip()
        if not line:
            continue
        try:
            obj=json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict) and "claude_session_id" in obj:
            sys.stdout.write(obj.get("claude_session_id") or "")
            break
except Exception:
    pass' 2>/dev/null || true)"
    CLAUDE_SESSION_ID="${PARSED:-}"
fi

# Defaults per research Decision 2.
AREA="${SPLOCK_INTENT_AREA:-unscoped_interactive}"

# Build the register command. Use absolute path resolved from REPO_ROOT
# per recon §5.1 / cross-cutting constraint (PATH-based lookup unsafe).
INTENT_BIN="$REPO_ROOT/bin/intent"

# Subprocess invocation wrapped by:
#   - 5s timeout backstop (`timeout 5 <cmd>` returns 124 on hit)
#   - env-var override forcing log_only collision halt action
#   - env-var fast-path skipping MySQL cold-cache cost
#   - `|| true` so any non-zero exit (including timeout) fails open
REGISTER_ARGS=(
    "register"
    "--area" "$AREA"
    "--paths" "_unscoped"
    "--kind" "interactive"
    "--closure" "session_timeout:240m"
    "--emitted-by" "session_start_auto"
    "--json"
)
if [ -n "$CLAUDE_SESSION_ID" ]; then
    REGISTER_ARGS+=("--claude-session-id" "$CLAUDE_SESSION_ID")
fi

# Capture stderr to a tmp so we can route it to hook-log on failure
# without polluting the SessionStart hook's stdout/stderr contract.
TMP_STDERR="$(mktemp 2>/dev/null || echo /tmp/splock-session-start-stderr.$$)"
REGISTER_RC=0
if [ -x "$INTENT_BIN" ]; then
    SPLOCK_INTENT_COLLISION_HALT_ACTION=log_only \
    SPLOCK_INTENT_AUTO_REGISTER_INTERACTIVE=1 \
        timeout 5 "$INTENT_BIN" "${REGISTER_ARGS[@]}" >/dev/null 2>"$TMP_STDERR" \
        || REGISTER_RC=$?
else
    REGISTER_RC=127  # bin/intent missing / not executable
fi

if [ "$REGISTER_RC" -ne 0 ]; then
    DETAIL="auto-register subprocess exit=$REGISTER_RC"
    if [ -s "$TMP_STDERR" ]; then
        STDERR_HEAD="$(head -c 160 "$TMP_STDERR" 2>/dev/null || true)"
        DETAIL="$DETAIL stderr=$STDERR_HEAD"
    fi
    "$REPO_ROOT/bin/hook-log" splock-session-start error "$DETAIL" >/dev/null 2>&1 || true
else
    "$REPO_ROOT/bin/hook-log" splock-session-start ok "auto-register ok" >/dev/null 2>&1 || true
fi

# Cleanup tmp file (best-effort; ignore failure).
rm -f "$TMP_STDERR" 2>/dev/null || true

# T5 — doctor lazy trigger. Fires after auto-register so the operator's
# SessionStart event drives a doctor sweep if `~/.intent_doctor_last_run`
# is stale. Background-forked inside the Python helper (Popen detached
# with start_new_session=True) — the foreground returns in <50ms. The
# `timeout 5` is a guard against pathological flock contention. `|| true`
# preserves the SessionStart fail-open contract.
timeout 5 python -c "import sys; sys.path.insert(0, '$REPO_ROOT'); from bin._intent import doctor_trigger; doctor_trigger.trigger_background()" >/dev/null 2>&1 || true

# Always exit 0 per SessionStart contract.
exit 0
