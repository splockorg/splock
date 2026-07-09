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
    "hooks_dir",
    "sealed_paths_file",
]


def hooks_dir():
    """Directory holding the shipped hook scripts + the seal inventory.

    This fork keeps hooks at the TOP LEVEL (`hooks/`); upstream nests them under
    `.claude/hooks/`. Modules that hardcoded the nested path silently found
    nothing here — `hook_lint --check` reported "0 hooks PASS", and the two
    sealed-path guards took their `FileNotFoundError -> allow` branch, failing
    open. Resolve through one function so that mistake cannot be made per-module.
    """
    from pathlib import Path

    from bin._env_paths import plugin_root

    return plugin_root() / "hooks"


def sealed_paths_file():
    """Resolve the canonical sealed-path inventory.

    Order: the `SPLOCK_SEALED_PATHS_FILE` test override, then an adopter-supplied
    list in the invoking repo (either layout), then the plugin-shipped one.

    A missing inventory means NO DEFENSE: both `sealed_paths_hook` and
    `sealed_delete_hook` allow the operation and log a forensic note. Returning a
    path that exists is therefore load-bearing, not cosmetic.
    """
    import os
    from pathlib import Path

    override = os.environ.get("SPLOCK_SEALED_PATHS_FILE", "").strip()
    if override:
        return Path(override)
    for candidate in (
        Path("hooks/sealed_paths.txt"),
        Path(".claude/hooks/sealed_paths.txt"),
    ):
        if candidate.exists():
            return candidate
    return hooks_dir() / "sealed_paths.txt"
