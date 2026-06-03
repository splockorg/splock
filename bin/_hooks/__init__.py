"""Python helpers for `.claude/hooks/` shell hooks.

Per implplan §G.impl.2 file tree:
- ``pattern_detect`` — suppression / install / DDL / settings regex sets
- ``manifest_read`` — `_chain_sessions.json` reader (flock-aware)
- ``staged_input`` — per-iteration Sonnet-input appender (§G.4)
- ``registry_query`` — PyPI / npm metadata + cache (§G.7.1)
- ``log_emit`` — `bin/hook-log` core
- ``sealed_paths`` — glob loader + matcher for the canonical inventory

Hook scripts import their classifiers from these modules (single source
of truth per cross-cutting convention line 233).

Closed-enum exit codes for ``bin/hook-lint`` (per §G.impl.12 table).
"""

from __future__ import annotations

# bin/hook-lint validation-rule closed-enum exit codes (§G.impl.12).
# Scope is hook-lint-only and NOT in the chain-orchestrated shared
# registry per A.impl.3a (the numerical overlap at 10 is intentional
# and scope-disambiguated).
HOOK_LINT_EXIT_OK: int = 0
HOOK_LINT_EXIT_USAGE: int = 1
HOOK_LINT_EXIT_R_JSON_SHAPE: int = 10
HOOK_LINT_EXIT_R_EXIT_ZERO_ON_DENY: int = 11
HOOK_LINT_EXIT_R_STOP_HOOK_ACTIVE: int = 12
HOOK_LINT_EXIT_R_NAMING_KEBAB: int = 13
HOOK_LINT_EXIT_R_HOOK_LOG_CALL: int = 14
HOOK_LINT_EXIT_R_POSTTOOL_NO_DENY: int = 15
HOOK_LINT_EXIT_R_PACKAGE_SAFETY_CITATION: int = 16


HOOK_LINT_RULES: tuple[tuple[str, str, int], ...] = (
    ("R-JSON-SHAPE",
     "PreToolUse refusal stdout MUST be valid JSON matching the "
     "hookSpecificOutput shape.",
     HOOK_LINT_EXIT_R_JSON_SHAPE),
    ("R-EXIT-ZERO-ON-DENY",
     "PreToolUse hooks MUST exit 0 on refusal (never exit 2).",
     HOOK_LINT_EXIT_R_EXIT_ZERO_ON_DENY),
    ("R-STOP-HOOK-ACTIVE",
     "Stop hooks MUST inspect stop_hook_active and no-op when true.",
     HOOK_LINT_EXIT_R_STOP_HOOK_ACTIVE),
    ("R-NAMING-KEBAB",
     "Hook filenames MUST be kebab-case .sh files.",
     HOOK_LINT_EXIT_R_NAMING_KEBAB),
    ("R-HOOK-LOG-CALL",
     "Every hook MUST call bin/hook-log with a closed-enum action.",
     HOOK_LINT_EXIT_R_HOOK_LOG_CALL),
    ("R-POSTTOOL-NO-DENY",
     "PostToolUse hooks MUST NOT emit JSON deny.",
     HOOK_LINT_EXIT_R_POSTTOOL_NO_DENY),
    ("R-PACKAGE-SAFETY-CITATION",
     "package-safety.sh header MUST cite "
     "research_findings_v1.md §D + §G.",
     HOOK_LINT_EXIT_R_PACKAGE_SAFETY_CITATION),
)


# Closed-enum action vocabulary for bin/hook-log (§G.impl.11).
HOOK_LOG_ACTIONS: frozenset[str] = frozenset({"ok", "blocked", "flagged", "error"})


__all__ = [
    "HOOK_LINT_EXIT_OK",
    "HOOK_LINT_EXIT_USAGE",
    "HOOK_LINT_EXIT_R_JSON_SHAPE",
    "HOOK_LINT_EXIT_R_EXIT_ZERO_ON_DENY",
    "HOOK_LINT_EXIT_R_STOP_HOOK_ACTIVE",
    "HOOK_LINT_EXIT_R_NAMING_KEBAB",
    "HOOK_LINT_EXIT_R_HOOK_LOG_CALL",
    "HOOK_LINT_EXIT_R_POSTTOOL_NO_DENY",
    "HOOK_LINT_EXIT_R_PACKAGE_SAFETY_CITATION",
    "HOOK_LINT_RULES",
    "HOOK_LOG_ACTIONS",
]
