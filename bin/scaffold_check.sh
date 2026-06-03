#!/usr/bin/env bash
# bin/scaffold_check.sh — existence/structure assertions for the splock scaffold.
#
# Proves the scaffold exists as intended:
#   - every plugin-layout dir + every plan-state scaffold file is present
#   - the framework-surface dirs are EMPTY except for their .gitkeep placeholder
#   - the repo is NOT git-initialized at the scaffold stage
#
# Self-contained POSIX/bash; no venv, no python. Exit 0 = all assertions pass.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail=0
pass=0

assert_dir() {
  if [ -d "$ROOT/$1" ]; then
    pass=$((pass + 1))
  else
    echo "FAIL: expected directory missing: $1" >&2
    fail=$((fail + 1))
  fi
}

assert_file() {
  if [ -f "$ROOT/$1" ]; then
    pass=$((pass + 1))
  else
    echo "FAIL: expected file missing: $1" >&2
    fail=$((fail + 1))
  fi
}

# Asserts dir contains ONLY a single .gitkeep (the empty-skeleton contract).
assert_empty_skeleton() {
  local d="$ROOT/$1"
  if [ ! -d "$d" ]; then
    echo "FAIL: expected skeleton dir missing: $1" >&2
    fail=$((fail + 1))
    return
  fi
  # Count all entries (files + subdirs), excluding . and ..
  local n
  n="$(find "$d" -mindepth 1 | wc -l | tr -d ' ')"
  if [ ! -f "$d/.gitkeep" ]; then
    echo "FAIL: skeleton dir $1 missing .gitkeep" >&2
    fail=$((fail + 1))
    return
  fi
  if [ "$n" != "1" ]; then
    echo "FAIL: skeleton dir $1 must contain ONLY .gitkeep at the scaffold stage; found $n entries" >&2
    find "$d" -mindepth 1 >&2
    fail=$((fail + 1))
    return
  fi
  pass=$((pass + 1))
}

# --- plugin-layout dirs, EMPTY skeleton (filled by the gather phase) ---
for d in agents commands skills hooks bin schemas .claude-plugin; do
  assert_dir "$d"
done
# bin/ legitimately holds this check script + .gitkeep at the scaffold stage,
# so it is NOT asserted empty; the rest of the framework dirs must be
# .gitkeep-only.
for d in agents commands skills hooks schemas .claude-plugin; do
  assert_empty_skeleton "$d"
done

# --- runtime-state convention dir ---
assert_dir ".plugin-data"
assert_file ".plugin-data/.gitkeep"

# --- plan-state scaffolding ---
assert_dir "docs/plans"
assert_file "docs/plans/.gitkeep"
assert_dir "docs/plans/scheduled_markers"
assert_file "docs/plans/scheduled_markers/list.md"
assert_file "docs/plans/scheduled_markers/prefix_registry.md"
assert_file "docs/plans/scheduled_markers/closed_archive.md"

# --- outstanding_issues.md at repo root ---
assert_file "outstanding_issues.md"

# --- scaffold manifest ---
assert_file "SCAFFOLD.md"
assert_file "bin/scaffold_check.sh"

# --- the scaffold stage must NOT have git-initialized the repo (the gather
#     phase does that) ---
if [ -d "$ROOT/.git" ]; then
  echo "FAIL: .git exists — the scaffold stage must leave the repo un-git-initialized" >&2
  fail=$((fail + 1))
else
  pass=$((pass + 1))
fi

# --- the scaffold stage must NOT have copied any framework content yet ---
# Guard: no .claude-plugin manifest files exist at the scaffold stage (the
# gather phase authors them).
if [ -f "$ROOT/.claude-plugin/plugin.json" ] || [ -f "$ROOT/.claude-plugin/marketplace.json" ]; then
  echo "FAIL: .claude-plugin manifest present — that is the gather phase's deliverable" >&2
  fail=$((fail + 1))
else
  pass=$((pass + 1))
fi

echo "scaffold_check: ${pass} passed, ${fail} failed"
if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "OK: splock T0 scaffold structure verified."
