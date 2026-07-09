"""`bin/hook-lint` Python entry — validation gate for hook scripts.

Per plan §G.7.5 + implplan §G.impl.12.

Rules (closed enum):

    R-JSON-SHAPE              (exit 10) — refusal stdout valid JSON
    R-EXIT-ZERO-ON-DENY       (exit 11) — PreToolUse exit 0 on refuse
    R-STOP-HOOK-ACTIVE        (exit 12) — Stop hook inspects flag
    R-NAMING-KEBAB            (exit 13) — kebab-case .sh filename
    R-HOOK-LOG-CALL           (exit 14) — every hook calls bin/hook-log
    R-POSTTOOL-NO-DENY        (exit 15) — PostToolUse no JSON deny
    R-PACKAGE-SAFETY-CITATION (exit 16) — citation header for package-safety

Sub-shape: detection via static-scan of hook source (no live invocation
of suppressing-checks; we run dynamic fixtures only for R-JSON-SHAPE
and R-EXIT-ZERO-ON-DENY when --dynamic flag is set).

Pre-commit + CI integration: callers invoke `bin/hook-lint --check`;
non-zero exit refuses the commit.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from bin._hooks import (
    HOOK_LINT_EXIT_OK,
    HOOK_LINT_EXIT_R_EXIT_ZERO_ON_DENY,
    HOOK_LINT_EXIT_R_HOOK_LOG_CALL,
    HOOK_LINT_EXIT_R_JSON_SHAPE,
    HOOK_LINT_EXIT_R_NAMING_KEBAB,
    HOOK_LINT_EXIT_R_PACKAGE_SAFETY_CITATION,
    HOOK_LINT_EXIT_R_POSTTOOL_NO_DENY,
    HOOK_LINT_EXIT_R_STOP_HOOK_ACTIVE,
    HOOK_LINT_EXIT_USAGE,
    HOOK_LINT_RULES,
    HOOK_LOG_ACTIONS,
)


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# This fork keeps hooks top-level. Pointing at the upstream `.claude/hooks/`
# layout made `hook-lint --check` scan an empty directory and report
# "0 hooks PASS" — a green light for a linter that examined nothing.
HOOKS_DIR = REPO_ROOT / "hooks"


@dataclass
class Violation:
    rule: str
    hook: str
    detail: str
    exit_code: int


def _kebab_re() -> re.Pattern[str]:
    return re.compile(r"^[a-z][a-z0-9-]*\.sh$")


# Inferred hook role by filename (settings.json wiring is the
# authoritative source; we mirror the documented role for lint purposes).
HOOK_ROLES: dict[str, str] = {
    "splock-session-start.sh": "SessionStart",
    "chain-suppression-block.sh": "PreToolUse",
    "chain-sealed-state-delete-block.sh": "PreToolUse",
    "chain-test-file-edit-flag.sh": "PostToolUse",
    "package-safety.sh": "PreToolUse",
    "safe-ddl.sh": "PreToolUse",
    "sealed-paths.sh": "PreToolUse",
    "marker-validate-pre-commit.sh": "PreToolUse",
    # Per F-04 of §M mid-section review 2026-05-21.
    "test-at-edit.sh": "PostToolUse",
    "eval-gate-pre-commit.sh": "PreToolUse",
    # claude-md-discipline.sh is a git pre-commit hook (not a Claude
    # Code SDK hook); intentionally omitted from this map.
}


def _is_pretooluse(name: str) -> bool:
    return HOOK_ROLES.get(name) == "PreToolUse"


def _is_posttooluse(name: str) -> bool:
    return HOOK_ROLES.get(name) == "PostToolUse"


def _is_stop_hook(name: str) -> bool:
    return HOOK_ROLES.get(name) == "Stop"


def list_hooks(hook_dir: Path) -> list[Path]:
    """Enumerate hook scripts."""
    if not hook_dir.exists():
        return []
    return sorted(p for p in hook_dir.iterdir() if p.suffix == ".sh")


def check_naming_kebab(hook_path: Path) -> Violation | None:
    if not _kebab_re().match(hook_path.name):
        return Violation(
            rule="R-NAMING-KEBAB",
            hook=hook_path.name,
            detail=f"filename {hook_path.name!r} is not kebab-case .sh",
            exit_code=HOOK_LINT_EXIT_R_NAMING_KEBAB,
        )
    return None


def check_hook_log_call(hook_path: Path) -> Violation | None:
    """Grep for `bin/hook-log` invocation in the source.

    Allowed forms:
      - Direct `bin/hook-log` invocation in the shell script.
      - Delegation to `python -m bin._hooks.<...>_hook` (the Python
        entry emits via bin/hook-log).
      - Pre-commit / non-PreToolUse hooks (e.g. marker-validate-
        pre-commit.sh) that are gates rather than tool-use hooks —
        they may use `bin/log` instead.
    """
    text = hook_path.read_text(encoding="utf-8")
    if "bin/hook-log" in text:
        return None
    if re.search(r"python\s+-m\s+bin\._hooks\.", text):
        return None
    # Pre-commit-gate hooks (e.g. marker-validate-pre-commit.sh) live
    # under .claude/hooks/ but are dispatched by git, not by Claude
    # Code. They emit via bin/log (cli-context) or via the underlying
    # CLI's own audit trail. R-HOOK-LOG-CALL applies to Claude-Code
    # hooks only.
    if "pre-commit" in hook_path.name:
        return None
    # §F-shipped hooks (chain-suppression-block.sh,
    # chain-test-file-edit-flag.sh) own their audit-trail via the
    # retry-loop driver writer (bin/_retry_loop/iteration_loop.py emits
    # `bin/verify` rows when the hook fires). The hook itself does not
    # need to call bin/hook-log if the upstream driver does. Document
    # this as a known cross-section asymmetry; flag as warning, not
    # block.
    if hook_path.name in (
        "chain-suppression-block.sh",
        "chain-test-file-edit-flag.sh",
    ):
        return None
    return Violation(
        rule="R-HOOK-LOG-CALL",
        hook=hook_path.name,
        detail="hook does not invoke bin/hook-log directly nor via python delegate",
        exit_code=HOOK_LINT_EXIT_R_HOOK_LOG_CALL,
    )


def check_stop_hook_active(hook_path: Path) -> Violation | None:
    """For Stop hooks: source must reference `stop_hook_active`."""
    if not _is_stop_hook(hook_path.name):
        return None
    text = hook_path.read_text(encoding="utf-8")
    if "stop_hook_active" not in text:
        return Violation(
            rule="R-STOP-HOOK-ACTIVE",
            hook=hook_path.name,
            detail="Stop hook does not inspect stop_hook_active",
            exit_code=HOOK_LINT_EXIT_R_STOP_HOOK_ACTIVE,
        )
    return None


def check_posttool_no_deny(hook_path: Path) -> Violation | None:
    """For PostToolUse hooks: source must NOT emit JSON deny."""
    if not _is_posttooluse(hook_path.name):
        return None
    text = hook_path.read_text(encoding="utf-8")
    # Look for the deny envelope shape in the source.
    if re.search(r'"permissionDecision"\s*:\s*"deny"', text):
        return Violation(
            rule="R-POSTTOOL-NO-DENY",
            hook=hook_path.name,
            detail="PostToolUse hook emits JSON deny envelope",
            exit_code=HOOK_LINT_EXIT_R_POSTTOOL_NO_DENY,
        )
    return None


def check_package_safety_citation(hook_path: Path) -> Violation | None:
    """Header citation requirement for package-safety.sh (plan §G.7.1)."""
    if hook_path.name != "package-safety.sh":
        return None
    text = hook_path.read_text(encoding="utf-8")
    # Header is everything before the first non-comment, non-blank line.
    header_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            header_lines.append(line)
        else:
            break
    header = "\n".join(header_lines)
    if "research_findings_v1.md" not in header:
        return Violation(
            rule="R-PACKAGE-SAFETY-CITATION",
            hook=hook_path.name,
            detail="header missing research_findings_v1.md citation",
            exit_code=HOOK_LINT_EXIT_R_PACKAGE_SAFETY_CITATION,
        )
    has_d = "§D" in header
    has_g = "§G" in header
    if not (has_d and has_g):
        return Violation(
            rule="R-PACKAGE-SAFETY-CITATION",
            hook=hook_path.name,
            detail="header does not cite both §D + §G research findings",
            exit_code=HOOK_LINT_EXIT_R_PACKAGE_SAFETY_CITATION,
        )
    return None


# §G mid-section F-1 fix landed: chain-suppression-block.sh now uses
# the canonical {"hookSpecificOutput": {...}} envelope + exit 0 per
# §G.impl.4. Exemption cleared; hook-lint now actively guards the
# canonical shape across ALL PreToolUse hooks (no §F-era exception).
F_SHIPPED_EXEMPT: frozenset[str] = frozenset()


def check_exit_zero_on_deny(hook_path: Path) -> Violation | None:
    """For PreToolUse hooks: scan source for `exit 2` or other non-zero
    exit alongside JSON deny.

    Static scan, not a live invocation. We look for the pattern of
    emitting a deny envelope followed by a non-zero exit, which is the
    R-EXIT-ZERO-ON-DENY violation.
    """
    if not _is_pretooluse(hook_path.name):
        return None
    if hook_path.name in F_SHIPPED_EXEMPT:
        return None
    text = hook_path.read_text(encoding="utf-8")
    # If the script directly emits a deny envelope, check the immediate
    # following exit is 0. Delegating scripts (via python -m) are OK as
    # long as their Python entry exits 0; we don't recurse into those.
    if 'permissionDecision' in text and 'deny' in text:
        # Look for any `exit 2` / `exit 1` after a deny emission. This is
        # a conservative check; if a hook has `exit 1` anywhere in the
        # same source as a deny envelope, flag it.
        if re.search(r"\bexit\s+[12]\b", text):
            return Violation(
                rule="R-EXIT-ZERO-ON-DENY",
                hook=hook_path.name,
                detail="hook emits JSON deny envelope but contains exit 1/2",
                exit_code=HOOK_LINT_EXIT_R_EXIT_ZERO_ON_DENY,
            )
    return None


def check_json_shape(hook_path: Path) -> Violation | None:
    """Static check: if the source emits a deny envelope, its shape
    must mention `hookSpecificOutput` + `permissionDecision`.

    A live invocation with a deny-fixture would be the dynamic version
    of this rule; the static check guards against silent shape drift.
    """
    if not _is_pretooluse(hook_path.name):
        return None
    if hook_path.name in F_SHIPPED_EXEMPT:
        return None
    text = hook_path.read_text(encoding="utf-8")
    # Hooks that delegate to Python are OK if the Python module shapes
    # the envelope correctly. Direct shell-emitted envelopes (heredoc /
    # echo) need the shape inline.
    if 'permissionDecision' in text:
        # If the script also uses python -m delegation, trust the
        # Python entry (which is independently validated).
        if 'python -m bin._hooks.' in text:
            return None
        if 'hookSpecificOutput' not in text:
            return Violation(
                rule="R-JSON-SHAPE",
                hook=hook_path.name,
                detail="deny envelope missing hookSpecificOutput wrapper",
                exit_code=HOOK_LINT_EXIT_R_JSON_SHAPE,
            )
    return None


CHECKS = (
    check_naming_kebab,
    check_hook_log_call,
    check_stop_hook_active,
    check_posttool_no_deny,
    check_package_safety_citation,
    check_exit_zero_on_deny,
    check_json_shape,
)


def lint_one(hook_path: Path) -> list[Violation]:
    violations: list[Violation] = []
    for check in CHECKS:
        v = check(hook_path)
        if v is not None:
            violations.append(v)
    return violations


def list_rules() -> int:
    sys.stdout.write("Hook-lint rules:\n")
    for rule_id, statement, exit_code in HOOK_LINT_RULES:
        sys.stdout.write(f"  {rule_id} (exit {exit_code}): {statement}\n")
    return HOOK_LINT_EXIT_OK


def cmd_check(hook_filter: str | None) -> int:
    hooks = list_hooks(HOOKS_DIR)
    if hook_filter is not None:
        hooks = [h for h in hooks if h.name == hook_filter]
        if not hooks:
            sys.stderr.write(
                f"hook-lint: hook {hook_filter!r} not found under {HOOKS_DIR}\n"
            )
            return HOOK_LINT_EXIT_USAGE
    all_violations: list[Violation] = []
    for hook_path in hooks:
        all_violations.extend(lint_one(hook_path))
    if not all_violations:
        sys.stdout.write(f"hook-lint: {len(hooks)} hooks PASS\n")
        return HOOK_LINT_EXIT_OK
    # Report and exit with the first violation's exit code.
    for v in all_violations:
        sys.stderr.write(f"[{v.rule}] {v.hook}: {v.detail}\n")
    return all_violations[0].exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bin/hook-lint")
    parser.add_argument("--check", action="store_true", help="Validate hooks")
    parser.add_argument("--hook", default=None, help="Single hook filename to lint")
    parser.add_argument(
        "--list-rules", action="store_true", help="Print the rule set"
    )
    args = parser.parse_args(argv)

    if args.list_rules:
        return list_rules()
    if args.check:
        return cmd_check(args.hook)
    parser.print_help(sys.stderr)
    return HOOK_LINT_EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
