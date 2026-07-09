"""C.12 — Verifier `NEEDS_HUMAN` verdict has documented halt contract.

Per inventory + userguide §3.7 ("`NEEDS_HUMAN` | The verifier cannot
determine status (ambiguous output, structural error). Chain halts;
operator inspects.") + verifier.md prompt declaration ("Answer
`NEEDS_HUMAN` if the situation is structurally ambiguous").

Verifies the three load-bearing contract claims:
  (a) The verifier subagent's prompt declares NEEDS_HUMAN as a valid
      verdict, distinct from READY and NO.
  (b) The userguide §3.7 verifier-verdict table documents NEEDS_HUMAN
      with "chain halts" semantics.
  (c) The retry-loop's IterationResult closed enum has a branch
      reachable from a NEEDS_HUMAN-class verdict (currently the
      verifier verdict is consumed via exit code; NEEDS_HUMAN-specific
      branching is the contract claim under test).

If (c) fails, the verifier prompt declares a verdict the substrate has
no specific halt-and-handoff path for — a documented-vs-implementation
gap. Per the per-pass discipline (Pass 7 prompt), real gaps surface in
the findings memo for operator triage rather than being silently fixed.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def test_verifier_prompt_declares_needs_human(repo_root):
    """C.12a: verifier.md frontmatter description + body cite NEEDS_HUMAN."""
    verifier_md = (repo_root / "agents" / "verifier.md").read_text(
        encoding="utf-8"
    )
    assert "NEEDS_HUMAN" in verifier_md, (
        "verifier.md must declare NEEDS_HUMAN as a valid verdict per "
        "plan §D.8.7 + Hole H.19 verdict closed enum"
    )
    # Cross-reference: the README/agent prompt should explain WHEN to use it.
    # Walk every occurrence (the first is in the frontmatter description) and
    # accept the body explanation that names "ambiguous" or "structural".
    md_lower = verifier_md.lower()
    explanation_found = False
    start = 0
    while True:
        idx = md_lower.find("needs_human", start)
        if idx == -1:
            break
        window = md_lower[idx : idx + 200]
        if "ambig" in window or "structural" in window:
            explanation_found = True
            break
        start = idx + len("needs_human")
    assert explanation_found, (
        "verifier.md mentions NEEDS_HUMAN but no occurrence is followed by an "
        "ambiguity / structural-error explanation within 200 chars. The "
        "verifier prompt should explain WHEN to answer NEEDS_HUMAN — otherwise "
        "the Haiku model has no anchor for the disposition."
    )


def test_userguide_documents_needs_human_halt_semantics(repo_root):
    """C.12b: userguide §3.7 verifier verdict table has a NEEDS_HUMAN row."""
    text = (repo_root / "docs" / "guides" / "splock_userguide.md").read_text(
        encoding="utf-8"
    )
    # Find §3.7 block.
    import re
    m = re.search(r"### 3\.7 .+?\n(.+?)(?:\n### |\n## )", text, re.DOTALL)
    assert m, "userguide §3.7 verifier section not found"
    section = m.group(1)
    assert "NEEDS_HUMAN" in section, (
        "userguide §3.7 must list NEEDS_HUMAN as a verifier verdict; "
        "section snippet:\n" + section[:500]
    )
    nh_row_idx = section.find("NEEDS_HUMAN")
    row_window = section[nh_row_idx : nh_row_idx + 300].lower()
    assert "halt" in row_window or "inspect" in row_window or "operator" in row_window, (
        "NEEDS_HUMAN row should document halt + operator-inspect semantics. "
        f"Window: {row_window!r}"
    )


def test_iteration_loop_has_distinct_needs_human_branch(repo_root):
    """C.12c: IterationResult enum branches reachable from a NEEDS_HUMAN verdict.

    The verifier subagent declares NEEDS_HUMAN as a distinct verdict
    (per (a) + (b)), so the retry loop should have a structurally
    distinct halt path for it — otherwise NEEDS_HUMAN collapses into
    either retry (FAILED_RETRY) or generic cap-exhaust (HALT_CAP_EXHAUSTED),
    silently dropping the verifier's actual disposition.

    Today (2026-05-22): the IterationResult enum is `PASSED /
    FAILED_RETRY / HALT_TAMPERING / HALT_BUDGET / HALT_CAP_EXHAUSTED`.
    A `HALT_NEEDS_HUMAN` (or equivalent name) branch is absent —
    documented-vs-implementation gap.

    xfail until a §F.impl follow-up adds the explicit branch + the
    chain driver maps it to an exit code + morning-review entry.
    """
    src = (repo_root / "bin" / "_retry_loop" / "iteration_loop.py").read_text(
        encoding="utf-8"
    )
    # Look for any HALT_NEEDS_HUMAN-class branch.
    has_branch = any(
        token in src
        for token in (
            "HALT_NEEDS_HUMAN",
            "NEEDS_HUMAN",
            "needs_human",
        )
    )
    if not has_branch:
        pytest.xfail(
            "verifier NEEDS_HUMAN verdict has no distinct branch in "
            "bin/_retry_loop/iteration_loop.py — IterationResult enum lacks "
            "HALT_NEEDS_HUMAN. Per userguide §3.7 contract claim, NEEDS_HUMAN "
            "should halt cleanly + queue operator inspection. Surfaced in "
            "Pass 7 findings memo for operator triage."
        )
    # If a branch landed, the test should pass; remove the xfail block
    # and tighten the assertion when that happens.
    assert has_branch


def test_morning_review_deferral_reasons_can_express_needs_human(repo_root):
    """C.12d: DEFERRAL_REASONS closed enum has a NEEDS_HUMAN-mapped value.

    Per userguide §3.7: NEEDS_HUMAN → chain halts → operator inspects.
    The substrate's "operator-inspects" surface is the morning-review
    daily file. A NEEDS_HUMAN halt should produce a morning-review entry
    with a `deferral_reason` distinguishable from `retry_exceeded` /
    `tampering_detected` / `budget_below_threshold` /
    `phase_boundary_review_exhausted` / `collision_detected`.

    Today there is no `verifier_ambiguous` / `needs_human` / `verifier_uncertain`
    entry in the closed enum. xfail with surfaced finding.
    """
    from bin._morning_review.entry_format import DEFERRAL_REASONS

    candidates = {"verifier_ambiguous", "needs_human", "verifier_uncertain",
                  "verifier_needs_human"}
    found = candidates & DEFERRAL_REASONS
    if not found:
        pytest.xfail(
            "DEFERRAL_REASONS closed enum has no NEEDS_HUMAN-mapped value "
            f"(checked candidates: {sorted(candidates)}; current enum: "
            f"{sorted(DEFERRAL_REASONS)}). Adding one is a v1.5-class "
            "additive bump per §C.impl.13 schema-bump policy. Surfaced in "
            "Pass 7 findings memo."
        )
    assert found, f"expected NEEDS_HUMAN entry in DEFERRAL_REASONS; got {sorted(DEFERRAL_REASONS)}"
