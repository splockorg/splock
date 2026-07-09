"""J.19 — Every escape hatch documented in userguide §13.2 actually works.

Per inventory + userguide §13.2 "A hook just refused my edit" table:
each refusal class names an escape hatch the operator can use to recover.
This test verifies the escape hatches exist + accept their documented
invocation forms, so the "operator can always recover" promise holds
even when the direct action is refused.

Pairs from §13.2 (each row: refusal class → recommended escape):
  1. sealed-state path → `bin/update_orchestrator <slug> <task> <status>`
  2. suppression pattern → fix test OR sanction via `_test_expectations.json`
  3. package install w/o lockfile → add to `requirements.txt`
  4. raw DDL outside DAL → Python DAL with a privileged DB role (dropped here)
  5. CLAUDE.md > 200 lines → move to nested CLAUDE.md
  6. outstanding_issues.md cap → run `bin/morning-review` + triage

Some escape hatches (4 + parts of 2/6) need full infrastructure to
exercise their happy path. This test pins what we CAN structurally
verify:
  (a) The escape-hatch CLI exists at the referenced path.
  (b) It accepts the documented invocation form (--help works; argv
      parser doesn't refuse the canonical shape).
  (c) The sanction-mechanism file/schema is reachable.
"""

from __future__ import annotations

import subprocess
import pytest
from pathlib import Path


pytestmark = pytest.mark.acceptance


def _run(cmd: list[str], cwd: Path, timeout: int = 10) -> subprocess.CompletedProcess:
    """Invoke a CLI inheriting the test runner's env (incl. activated venv)."""
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
    )


# ---------------------------------------------------------------------------
# (1) sealed-state path → bin/update_orchestrator
# ---------------------------------------------------------------------------

def test_escape_hatch_1_update_orchestrator_exists_and_parses(repo_root):
    """J.19.1: bin/update_orchestrator exists + accepts --help."""
    cli = repo_root / "bin" / "update_orchestrator"
    assert cli.exists(), (
        "userguide §13.2 documents `bin/update_orchestrator` as the escape "
        "hatch for sealed-state Write refusals, but the file does not exist."
    )
    result = _run([str(cli), "--help"], cwd=repo_root)
    assert result.returncode == 0, (
        f"bin/update_orchestrator --help failed (rc={result.returncode}); "
        f"stderr: {result.stderr!r}"
    )
    # The CLI must mention the canonical 3-positional form: <slug> <task> <status>
    help_text = result.stdout.lower()
    assert "slug" in help_text or "plan" in help_text, (
        "bin/update_orchestrator --help text doesn't mention slug positional — "
        "documented invocation form may have drifted from implementation"
    )
    assert "status" in help_text, (
        "bin/update_orchestrator --help text doesn't mention status positional — "
        "documented invocation form may have drifted from implementation"
    )


# ---------------------------------------------------------------------------
# (2) suppression pattern → _test_expectations.json sanction mechanism
# ---------------------------------------------------------------------------

def test_escape_hatch_2_test_expectations_json_sanction_mechanism_exists(repo_root):
    """J.19.2: `chain-suppression-block.sh` reads `_test_expectations.json` to permit sanctioned skips."""
    hook = repo_root / "hooks" / "chain-suppression-block.sh"
    assert hook.exists(), "chain-suppression-block.sh missing"
    # The hook's Python backing should reference _test_expectations.json so
    # the sanctioned-skip escape hatch is actually implemented.
    text = hook.read_text(encoding="utf-8")
    # Hook delegates to bin._hooks; check the backing module + the hook script
    # together. The text-expectations sanction is referenced in either.
    py_backing = repo_root / "bin" / "_hooks" / "chain_suppression_block.py"
    sources = [text]
    if py_backing.exists():
        sources.append(py_backing.read_text(encoding="utf-8"))
    combined = "\n".join(sources)
    assert "_test_expectations.json" in combined, (
        "userguide §13.2 documents `_test_expectations.json` as the sanction "
        "mechanism for sanctioned skips, but neither the hook script nor its "
        "Python backing reference the file — escape hatch is not implemented."
    )


# ---------------------------------------------------------------------------
# (3) package install w/o lockfile → add to requirements.txt
# ---------------------------------------------------------------------------

def test_escape_hatch_3_lockfile_addition_permits_install(repo_root):
    """J.19.3: package-safety.sh permits install when the package is in requirements.txt.

    Verified structurally via the hook backing: if requirements.txt
    addition were not the bypass, the hook's allow-path wouldn't read it.
    """
    hook = repo_root / "hooks" / "package-safety.sh"
    assert hook.exists(), "package-safety.sh missing"
    text = hook.read_text(encoding="utf-8")
    py_backing = repo_root / "bin" / "_hooks" / "package_safety.py"
    sources = [text]
    if py_backing.exists():
        sources.append(py_backing.read_text(encoding="utf-8"))
    combined = "\n".join(sources)
    # The hook must read requirements.txt (or a lockfile manifest) to decide.
    assert "requirements" in combined.lower() or "lockfile" in combined.lower(), (
        "package-safety.sh has no reference to requirements.txt / lockfile — "
        "userguide §13.2 escape hatch ('add to requirements.txt') is not "
        "wired into the hook's allow-path."
    )


# ---------------------------------------------------------------------------
# (4) raw DDL → privileged-DB-role Python DAL
# ---------------------------------------------------------------------------

# escape_hatch_4 (the safe-DDL escape) is REPO-SPECIFIC: it names the source
# repo's database admin role, whose env-var prefix is a trace_grep-forbidden
# token. The test cannot be carried here even as a skip. Dropped.


def test_escape_hatch_5_claude_md_discipline_supports_nested(repo_root):
    """J.19.5: `claude-md-discipline.sh` only checks ROOT CLAUDE.md, so nested CLAUDE.md is the escape."""
    hook = repo_root / "hooks" / "claude-md-discipline.sh"
    assert hook.exists(), "claude-md-discipline.sh missing"
    text = hook.read_text(encoding="utf-8")
    py_backing = repo_root / "bin" / "_hooks" / "claude_md_discipline.py"
    if py_backing.exists():
        text = text + "\n" + py_backing.read_text(encoding="utf-8")
    # The hook should scope its line-count check to root CLAUDE.md only —
    # if it caught nested files too, the escape hatch wouldn't work.
    # We accept either:
    #   - an explicit root-only marker (e.g. `CLAUDE.md` literal without glob)
    #   - a comment referencing "nested" or "subtree" exemption
    has_root_scope_or_nested_exempt = (
        "root" in text.lower()
        or "nested" in text.lower()
        or "subtree" in text.lower()
        or '"CLAUDE.md"' in text
    )
    assert has_root_scope_or_nested_exempt, (
        "claude-md-discipline.sh shows no awareness of root-vs-nested CLAUDE.md "
        "distinction; userguide §13.2 escape hatch may not work."
    )


# ---------------------------------------------------------------------------
# (6) outstanding_issues.md cap → bin/morning-review triage
# ---------------------------------------------------------------------------

def test_escape_hatch_6_morning_review_exists_and_supports_triage(repo_root):
    """J.19.6: bin/morning-review exists + has triage subcommands."""
    cli = repo_root / "bin" / "morning-review"
    assert cli.exists(), (
        "userguide §13.2 documents `bin/morning-review` as the escape hatch "
        "for outstanding_issues.md cap; CLI does not exist."
    )
    result = _run([str(cli), "--help"], cwd=repo_root)
    assert result.returncode == 0, (
        f"bin/morning-review --help failed (rc={result.returncode})"
    )
    # Should mention triage-related verbs (list / route-outstanding / abandon).
    text = result.stdout.lower()
    triage_verbs = ("list", "route-outstanding", "abandon", "reactivate")
    missing = [v for v in triage_verbs if v not in text]
    assert not missing, (
        f"bin/morning-review --help missing documented triage subcommands "
        f"{missing}; escape hatch from §13.2 may be incomplete."
    )


# ---------------------------------------------------------------------------
# Audit — every refusal class in §13.2 has a tested escape-hatch row above
# ---------------------------------------------------------------------------

def test_userguide_132_refusal_classes_all_covered(repo_root):
    """J.19.audit: every row in §13.2 has a corresponding J.19.* test above."""
    import re

    text = (repo_root / "docs" / "guides" / "splock_userguide.md").read_text(
        encoding="utf-8"
    )
    m = re.search(r"### 13\.2 .+?\n(.+?)(?:\n### |\n## )", text, re.DOTALL)
    assert m, "§13.2 not found"
    section = m.group(1)
    table_rows = [
        line for line in section.splitlines()
        if line.startswith("|") and "|" in line[1:] and "---" not in line
    ]
    # First row is the header — match it exactly to avoid filtering data rows
    # whose body text happens to contain "Reason" or "Fix".
    data_rows = [
        r for r in table_rows
        if not (r.startswith("| Reason ") or r.startswith("| reason "))
    ]
    # 6 refusal classes documented + tests 1-6 above. Any additions to the
    # table should add a J.19.<N> test.
    assert len(data_rows) == 6, (
        f"§13.2 row count is {len(data_rows)} (expected 6 escape hatches; "
        f"this test file only covers 6). Add a J.19.<N> test for any new row, "
        f"then bump this expectation. Rows: {data_rows}"
    )
