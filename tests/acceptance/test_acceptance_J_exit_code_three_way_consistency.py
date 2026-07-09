"""J.14 — Exit-code three-way consistency: registry ↔ userguide ↔ callsite.

Per inventory:
- Source: userguide §13.3 "Chain halted with exit code N" + plan §A.impl.3a
  master registry + "closed-enum exit codes" claim in implplan §A.impl.3a
  line 452-528.
- Expected outcome: extends J.7 (which audits implplan master-registry vs
  exit_codes.py constants) by adding the user-facing and call-site axes.
  The closed-enum claim is hollow if codes are registered but never
  raised — every operator-facing EXIT_* must surface from at least one
  call site under bin/.

Three axes for `bin/_chain_overnight/exit_codes.py` (the operator-facing
canonical registry):
  (1) Each EXIT_* constant has a non-trivial docstring (already enforced
      by code review; verified here).
  (2) Each EXIT_* constant is referenced from a `bin/_chain_overnight/`
      source file as a return value OR raise / sys.exit value (the
      closed-enum-is-actually-used invariant).
  (3) Numeric codes documented in userguide §13.3 align with EXIT_*
      constants (operator-facing surface matches code reality).

Codes not in §13.3 are universal/internal (EXIT_OK; EXIT_DRIVER_CRASH);
they appear in EXEMPT_FROM_USERGUIDE.
"""

from __future__ import annotations

import pytest
import re
from pathlib import Path


pytestmark = pytest.mark.acceptance


# Constants that intentionally don't appear in userguide §13.3 (operator
# never sees them as an interesting halt — success path or internal-only).
EXEMPT_FROM_USERGUIDE: frozenset[str] = frozenset({
    "EXIT_OK",            # success — not a halt
    "EXIT_DRIVER_CRASH",  # internal crash (operator inspects logs)
})

# Constants that don't need a separate call site under bin/_chain_overnight/
# because they're propagated from a downstream binary (planner / verify-plan)
# OR emitted by a sibling CLI package while still living in
# `bin/_chain_overnight/exit_codes.py` as the cross-CLI shared registry.
# Test (2) still passes if the constant is named in a propagation table OR
# in this set.
PROPAGATED_NOT_DIRECTLY_RAISED: frozenset[str] = frozenset({
    # CCOR.1 (R-exit-codes): EXIT_NOT_PAUSED is emitted by
    # `bin/_chain_resume/main.py` (T-6) on missing pause sentinel or
    # orphan-paused state; EXIT_ALREADY_PAUSED is emitted by
    # `bin/_chain_pause/main.py` (T-5) on second-pause race. Both live in
    # `bin/_chain_overnight/exit_codes.py` because that file is the
    # cross-CLI shared registry per A.impl.3a — the chain driver itself
    # does not raise these codes. Direct call sites exist in the
    # respective `bin/_chain_pause/` and `bin/_chain_resume/` packages.
    "EXIT_NOT_PAUSED",
    "EXIT_ALREADY_PAUSED",
})


EXIT_CONSTANT_RE = re.compile(
    # Matches both `EXIT_X = N` and `EXIT_X: int = N` (PEP 526 annotation).
    r"^(EXIT_[A-Z0-9_]+)(?:\s*:\s*[A-Za-z][A-Za-z0-9_\[\], ]*)?\s*=\s*(\d+)",
    re.MULTILINE,
)


def _collect_chain_exit_codes(repo_root: Path) -> dict[str, int]:
    text = (repo_root / "bin" / "_chain_overnight" / "exit_codes.py").read_text(
        encoding="utf-8"
    )
    return {m.group(1): int(m.group(2)) for m in EXIT_CONSTANT_RE.finditer(text)}


def _userguide_133_codes(repo_root: Path) -> set[int]:
    """Parse the §13.3 'Chain halted with exit code N' table for numeric codes."""
    text = (repo_root / "docs" / "guides" / "splock_userguide.md").read_text(
        encoding="utf-8"
    )
    # Find the §13.3 section block.
    m = re.search(r"### 13\.3 .+?\n(.+?)(?:\n### |\n## )", text, re.DOTALL)
    assert m, "userguide §13.3 section not found — anchor moved?"
    block = m.group(1)
    # Each row: `| <code> | <meaning> | <action> |` (leading whitespace + pipe).
    codes: set[int] = set()
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        parts = [c.strip() for c in line.strip("|").split("|")]
        if len(parts) >= 1 and parts[0].isdigit():
            codes.add(int(parts[0]))
    assert codes, "userguide §13.3 table parsed but no numeric codes found"
    return codes


def _chain_callsite_references(repo_root: Path) -> set[str]:
    """Collect every EXIT_* constant name referenced inside `bin/_chain_overnight/`."""
    chain_dir = repo_root / "bin" / "_chain_overnight"
    pattern = re.compile(r"\bEXIT_[A-Z0-9_]+\b")
    refs: set[str] = set()
    for path in chain_dir.rglob("*.py"):
        if path.name == "exit_codes.py":
            continue  # definitions don't count as call sites
        for match in pattern.finditer(path.read_text(encoding="utf-8", errors="ignore")):
            refs.add(match.group(0))
    return refs


def test_every_chain_exit_constant_has_docstring(repo_root):
    """J.14a: each EXIT_* in bin/_chain_overnight/exit_codes.py has a non-trivial docstring."""
    src = (repo_root / "bin" / "_chain_overnight" / "exit_codes.py").read_text(
        encoding="utf-8"
    )
    constants = _collect_chain_exit_codes(repo_root)

    missing_doc: list[str] = []
    for name in constants:
        # Match: EXIT_NAME = N\n"""..."""
        pattern = re.compile(
            rf"^{re.escape(name)}\s*=\s*\d+\s*\n\s*\"\"\"(.+?)\"\"\"",
            re.MULTILINE | re.DOTALL,
        )
        m = pattern.search(src)
        if not m or not m.group(1).strip():
            missing_doc.append(name)

    assert not missing_doc, (
        "Chain exit codes lacking a docstring:\n"
        + "\n".join(f"  - {n}" for n in missing_doc)
    )


def test_every_chain_exit_constant_used_at_a_callsite(repo_root):
    """J.14b: each EXIT_* in chain-overnight is referenced from a non-definition site."""
    constants = _collect_chain_exit_codes(repo_root)
    refs = _chain_callsite_references(repo_root)

    orphans: list[str] = []
    for name in constants:
        if name in PROPAGATED_NOT_DIRECTLY_RAISED:
            continue
        if name not in refs:
            orphans.append(name)

    assert not orphans, (
        "Chain-overnight EXIT_* constants declared but not referenced from any "
        "call site under bin/_chain_overnight/ (excluding the definitions file):\n"
        + "\n".join(f"  - {n}" for n in orphans)
        + "\n\nThe 'closed enum' claim is hollow if codes are declared but "
        "never raised — either delete the unused constant or add the call site."
    )


def test_userguide_133_codes_align_with_exit_constants(repo_root):
    """J.14c: each code in userguide §13.3 maps to a known EXIT_* (somewhere in bin/).

    Userguide §13.3 documents operator-facing codes — some are chain-driver
    own (EXIT_INSUFFICIENT_BUDGET = 5), others are propagated from
    downstream subsystems (EXIT_RETRY_EXCEEDED = 17 from §F, code 22 from
    morning-review, code 25 from escalation-trigger, etc.). For each code
    in the §13.3 table: assert SOME `bin/_*/exit_codes.py` defines it OR
    that it's documented as a propagated/cross-module code in the chain
    registry's PROPAGATED_FROM_* tables.
    """
    guide_codes = _userguide_133_codes(repo_root)

    # Collect numeric → EXIT_* from every bin/_*/exit_codes.py.
    all_codes_by_num: dict[int, set[str]] = {}
    for path in (repo_root / "bin").glob("_*/exit_codes.py"):
        text = path.read_text(encoding="utf-8")
        for m in EXIT_CONSTANT_RE.finditer(text):
            num = int(m.group(2))
            all_codes_by_num.setdefault(num, set()).add(m.group(1))

    unknown_in_userguide: list[int] = []
    for code in guide_codes:
        if code not in all_codes_by_num:
            unknown_in_userguide.append(code)

    assert not unknown_in_userguide, (
        "userguide §13.3 documents exit codes with no matching EXIT_* constant "
        "in any bin/_*/exit_codes.py:\n"
        + "\n".join(f"  - code {c}" for c in sorted(unknown_in_userguide))
        + "\n\nEither the userguide drifted (the code was removed) or a new "
        "code was documented without a registry entry. Reconcile both sides."
    )


def test_chain_exit_constants_documented_in_userguide_when_operator_facing(repo_root):
    """J.14d: each NON-exempt chain EXIT_* code appears in userguide §13.3.

    Operator-facing means the chain driver itself raises this code AND
    the operator's recovery action depends on knowing what the code means.
    Internal-only codes are listed in EXEMPT_FROM_USERGUIDE.
    """
    constants = _collect_chain_exit_codes(repo_root)
    guide_codes = _userguide_133_codes(repo_root)

    undocumented: list[tuple[str, int]] = []
    for name, num in constants.items():
        if name in EXEMPT_FROM_USERGUIDE:
            continue
        if num not in guide_codes:
            undocumented.append((name, num))

    assert not undocumented, (
        "Chain-overnight EXIT_* codes NOT documented in userguide §13.3 table "
        "(and not in EXEMPT_FROM_USERGUIDE):\n"
        + "\n".join(f"  - {n} (code {c})" for n, c in undocumented)
        + "\n\nEither add a row to userguide §13.3 OR add the constant to "
        "EXEMPT_FROM_USERGUIDE with rationale."
    )
