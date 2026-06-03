"""Reversibility-scope enforcement helpers — driver-side classification.

Per splock plan §F.4 + implplan §F.impl.4 (reversibility-scope
enforcement). This module documents the closed allowed/refused inventory
that the hook stack enforces:

- ``chain-suppression-block.sh`` PreToolUse refuses test-suppression
  patterns during the test-step retry window.
- ``chain-test-file-edit-flag.sh`` PostToolUse flags (does not block)
  test-file edits so R4 can fire.

The hook scripts themselves live at ``.claude/hooks/`` — see their inline
documentation for refusal logic. This module provides the driver-side
classification predicates the iteration loop calls when it needs to
inspect a path or pattern.

Distinction: this is the RUNTIME reversibility surface, scoped to the
test-step retry window inside `bin/chain-overnight`. It is NOT the
build-time reversibility framework that may exist for orchestrator-
level reviews (orchestrator §5 junctions).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable


# ----------------------------------------------------------------------
# Suppression-pattern inventory — plan §G.2 closed list per §F.4
# ----------------------------------------------------------------------

#: Python — ``sys.exit(0)`` inside source/test code (not test-only
#: scaffolding; the hook treats the body of a function/method as the
#: detection scope).
SUPPRESSION_PATTERNS_PYTHON: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsys\.exit\s*\(\s*0\s*\)"),
    re.compile(r"\bos\._exit\s*\(\s*0\s*\)"),
    re.compile(r"@pytest\.mark\.skip\b"),
    re.compile(r"@pytest\.mark\.xfail\b"),
    re.compile(r"\bpytest\.skip\s*\("),
    re.compile(r"\bpytest\.xfail\s*\("),
    re.compile(r"@unittest\.skip\b"),
    re.compile(r"@unittest\.expectedFailure\b"),
)

#: JavaScript / TypeScript family — process.exit, xit, .skip.
SUPPRESSION_PATTERNS_JS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bprocess\.exit\s*\(\s*0\s*\)"),
    re.compile(r"\bxit\s*\("),
    re.compile(r"\bxdescribe\s*\("),
    re.compile(r"\.skip\s*\("),
    re.compile(r"\.only\s*\("),
)

#: Java / JUnit — ``@Disabled`` / ``@Ignore``.
SUPPRESSION_PATTERNS_JAVA: tuple[re.Pattern[str], ...] = (
    re.compile(r"@Disabled\b"),
    re.compile(r"@Ignore\b"),
)

#: Cucumber — wip / skip tags.
SUPPRESSION_PATTERNS_CUCUMBER: tuple[re.Pattern[str], ...] = (
    re.compile(r"~@wip\b"),
    re.compile(r"@skip\b"),
)


def all_suppression_patterns() -> tuple[re.Pattern[str], ...]:
    """Return the closed union of plan §G.2 suppression patterns.

    The hook script invokes this regex set via Python; the driver-side
    classifier (`scan_for_suppression`) uses the same set for forensic
    classification (e.g., post-hoc analysis of why a hook fired).
    """
    return (
        *SUPPRESSION_PATTERNS_PYTHON,
        *SUPPRESSION_PATTERNS_JS,
        *SUPPRESSION_PATTERNS_JAVA,
        *SUPPRESSION_PATTERNS_CUCUMBER,
    )


def scan_for_suppression(content: str) -> list[str]:
    """Return matched suppression-pattern descriptions in `content`.

    Empty list means clean. Caller (the hook script) refuses on any
    non-empty list; the driver-side test surface inspects the list for
    forensic value (e.g., test fixtures asserting specific patterns get
    matched).
    """
    matches: list[str] = []
    for pat in all_suppression_patterns():
        if pat.search(content):
            matches.append(pat.pattern)
    return matches


# ----------------------------------------------------------------------
# Sanctioned-skip allowlist — `_test_expectations.json`
# ----------------------------------------------------------------------

def is_sanctioned_skip(
    pattern_matched: str,
    test_id: str,
    expectations: Iterable[dict],
) -> bool:
    """Return True iff `pattern_matched` for `test_id` is sanctioned.

    Per plan §G.2: tests that legitimately need to skip can be enumerated
    in ``_test_expectations.json``. The hook then permits the skip
    annotation as long as a matching entry exists.

    Each expectation entry must be a dict with at least:
    - ``test_id``: matches the test id being annotated
    - ``reason``: free-text justification (auditing only)
    - ``pattern``: optional pattern name to scope the allowlist
    """
    for entry in expectations:
        if not isinstance(entry, dict):
            continue
        if entry.get("test_id") != test_id:
            continue
        allowed_patterns = entry.get("pattern")
        if allowed_patterns is None:
            # No pattern specified → any suppression pattern allowed.
            return True
        if isinstance(allowed_patterns, str) and allowed_patterns == pattern_matched:
            return True
        if isinstance(allowed_patterns, list) and pattern_matched in allowed_patterns:
            return True
    return False


# ----------------------------------------------------------------------
# Test-file path classification — used by the PostToolUse flag hook
# ----------------------------------------------------------------------

TEST_FILE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)tests?/"),
    re.compile(r"_test\.py$"),
    re.compile(r"test_[^/]+\.py$"),
    re.compile(r"\.test\.[tj]sx?$"),
    re.compile(r"\.spec\.[tj]sx?$"),
)


def is_test_file(path: str | Path) -> bool:
    """Return True iff `path` resembles a test file location.

    Used by the PostToolUse ``chain-test-file-edit-flag`` hook to decide
    whether to append a flag entry to the staged Sonnet-input file.

    Path is treated as a string match — the hook fires regardless of
    whether the file exists yet (Edit-to-new-file case).
    """
    s = str(path)
    return any(pat.search(s) for pat in TEST_FILE_PATTERNS)


# ----------------------------------------------------------------------
# Hook activation window — SPLOCK_PLAN_SLUG / SPLOCK_CHAIN_ID / SPLOCK_PHASE
# ----------------------------------------------------------------------

def hook_active(
    *,
    std_plan_slug: str | None,
    std_chain_id: str | None,
    std_phase: int | str | None,
) -> bool:
    """Return True iff the test-step-only hooks should fire.

    Per §F.impl.4 hook scope window: ``chain-suppression-block`` and
    ``chain-test-file-edit-flag`` no-op when ``SPLOCK_PLAN_SLUG`` /
    ``SPLOCK_CHAIN_ID`` unset OR ``SPLOCK_PHASE != 5``. The always-on
    ``chain-sealed-state-delete-block`` is owned by §G — this helper
    does NOT short-circuit that hook.
    """
    if not std_plan_slug or not std_chain_id:
        return False
    if std_phase is None:
        return False
    try:
        phase_int = int(std_phase)
    except (TypeError, ValueError):
        return False
    return phase_int == 5


__all__ = [
    "SUPPRESSION_PATTERNS_CUCUMBER",
    "SUPPRESSION_PATTERNS_JAVA",
    "SUPPRESSION_PATTERNS_JS",
    "SUPPRESSION_PATTERNS_PYTHON",
    "TEST_FILE_PATTERNS",
    "all_suppression_patterns",
    "hook_active",
    "is_sanctioned_skip",
    "is_test_file",
    "scan_for_suppression",
]
