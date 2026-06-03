#!/usr/bin/env bash
# tests/trace_grep.sh — host-trace scrub CI gate (the single reusable script).
#
# SC-B "zero references" MUST-FIX bar. This is the ONE authoritative
# trace-grep script for the splock tree. It is delegated-to by:
#   * T-B  — run EARLY over the copied tree (the scrub gate).
#   * SC-E — doc-corpus trace-grep at first-commit time.
#   * T-F  — the authoritative full-tree pass (owns/finalizes this script).
#
# What it does:
#   1. Asserts NO binary build artifacts (__pycache__/, *.pyc, *.db) exist —
#      and PURGES them first, because a stale .pyc can carry provenance bytes
#      compiled from a not-yet-scrubbed .py and would defeat a text-only grep.
#   2. Greps the shipped tree for the host-identity pattern set over an
#      explicit extension scope PLUS the extensionless bin/ wrappers.
#   3. Applies the `git --porcelain` carve-out so legitimate
#      `git status --porcelain` flag lines are never counted as a host trace.
#
# Exit 0 = CLEAN (no host traces, no binary artifacts).
# Exit 1 = host-trace hit(s) found.
# Exit 2 = binary artifact present after purge (should never happen).
#
# Usage:  bash tests/trace_grep.sh [TREE_ROOT]
#         TREE_ROOT defaults to the repo root (parent of this script's dir).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${1:-$(cd "$SCRIPT_DIR/.." && pwd)}"
cd "$ROOT"

# ---------------------------------------------------------------------------
# The host-identity / host-residue pattern set. These are FIXED strings, not
# regexes; matched with grep -F so dots/dashes/underscores are literal.
# `billstagg`, `adsapphire`, `Bill Stagg`, `bill@adknown` are asserted-absent
# (no carve-out applies). `std_modulize` and `PP_DB` are likewise asserted-
# absent tree-wide; their only legitimate home is this gate's own definition
# files (the GATE_SELF carve-out below), exactly as for the other tokens.
# ---------------------------------------------------------------------------
PATTERNS=(
  "pp-extraction-automation"          # host repo name (covers .local $id host)
  "/home/bill"                        # host home path
  "standardization"                   # bare host design-slug (lowercase)
  "Standardization"                   # design-slug (capitalized prose)
  "billstagg"                         # personal handle — assert ABSENT
  "bill@adknown"                      # personal email — assert ABSENT
  "adknown"                           # host org
  "adsapphire"                        # host org/domain
  "Bill Stagg"                        # personal name — assert ABSENT
  "everybidet"                        # host sibling repo / brand
  "pp_extraction"                     # host DB user / schema token
  # --- T-F residue extension (operator-approved 2026-06-03) ---
  # Two host-build leaks that passed the original 11-pattern set but still
  # exposed where this tree came from. The build-provenance slug + the host
  # DB-env-var convention. Scrubbed in-place across the tree; pinned here so
  # they can never re-enter. NOTE: these are SPECIFIC safe tokens — the
  # build's task/SC IDs (e.g. ``T-A``/``SC-C``) are NOT gated here (a bare
  # task-ID regex would false-positive on legitimate prose tree-wide); that
  # provenance is handled by in-place docstring rewrites, not by this grep.
  "std_modulize"                      # host build-provenance plan slug
  "PP_DB"                             # host DB-env-var prefix (now SPLOCK_DB_*)
)

# Extension scope (SC-B explicit list).
EXTS=( "*.py" "*.json" "*.yaml" "*.yml" "*.md" "*.txt" "*.sh" "*.example" "*.json.example" )

# ---------------------------------------------------------------------------
# Step 1 — purge then assert binary-artifact absence (BEFORE grep).
# ---------------------------------------------------------------------------
find . -path ./.git -prune -o -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find . -path ./.git -prune -o -name '*.pyc' -type f -delete 2>/dev/null || true
rm -rf ./.pytest_cache 2>/dev/null || true

BIN_ARTIFACTS="$(find . -path ./.git -prune -o \( -name '__pycache__' -o -name '*.pyc' -o -name '*.db' \) -print 2>/dev/null)"
if [ -n "$BIN_ARTIFACTS" ]; then
  echo "TRACE-GREP FAIL: binary build artifacts present after purge:" >&2
  echo "$BIN_ARTIFACTS" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# Step 2 — build the candidate file list: extension scope + extensionless
# bin/ wrappers (the 40 POSIX wrappers carry no extension yet must be scanned;
# they previously held the host venv literal).
# ---------------------------------------------------------------------------
FIND_ARGS=()
first=1
for ext in "${EXTS[@]}"; do
  if [ "$first" -eq 1 ]; then FIND_ARGS+=( -name "$ext" ); first=0
  else FIND_ARGS+=( -o -name "$ext" ); fi
done

# Extensioned files anywhere (excluding .git) + every file directly under bin/
# (catches the extensionless wrappers).
#
# Self-exclusion carve-out: this gate's OWN definition files + the
# absence-asserting smoke-battery test necessarily enumerate the forbidden
# pattern set (they must, to grep for it / to assert it never re-enters) and
# would otherwise self-match. They carry no host identity — exclude them,
# exactly as a linter does not lint its own rule-definition file. Extend this
# list (and the mirror in tests/test_trace_scrub.py) whenever a new test
# literally spells a forbidden token to assert its absence.
GATE_SELF=(
  "./tests/trace_grep.sh"
  "./tests/test_trace_scrub.py"
  "./tests/test_smoke_battery.py"
)
GATE_SELF_ARGS=()
for f in "${GATE_SELF[@]}"; do GATE_SELF_ARGS+=( -e "$f" ); done
mapfile -t FILES < <(
  {
    find . -path ./.git -prune -o -type f \( "${FIND_ARGS[@]}" \) -print
    find ./bin -maxdepth 1 -type f -print 2>/dev/null
  } | sort -u | grep -vxF "${GATE_SELF_ARGS[@]}"
)

# ---------------------------------------------------------------------------
# Step 3 — grep each pattern; apply the git --porcelain carve-out.
# A matched line is EXCLUDED from the hit set if it is a legitimate
# `git ... --porcelain` invocation/reference (false positive, NOT scrubbed).
# ---------------------------------------------------------------------------
HITS=0
for pat in "${PATTERNS[@]}"; do
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    # Carve-out: drop lines that reference the git --porcelain flag.
    if printf '%s' "$line" | grep -qE 'git[^\n]*--porcelain|status[[:space:]]+--porcelain|--porcelain'; then
      continue
    fi
    echo "$line"
    HITS=$((HITS + 1))
  done < <(printf '%s\0' "${FILES[@]}" | xargs -0 grep -nF -- "$pat" 2>/dev/null)
done

if [ "$HITS" -ne 0 ]; then
  echo "" >&2
  echo "TRACE-GREP FAIL: $HITS host-trace hit(s) found (see above)." >&2
  exit 1
fi

echo "TRACE-GREP CLEAN: 0 host-identity traces across $(printf '%s\n' "${FILES[@]}" | wc -l | tr -d ' ') scanned files."
exit 0
