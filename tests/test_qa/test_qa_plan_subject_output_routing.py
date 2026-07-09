"""qa-on-plan loop — subject-stamped output routing + operator folding.

Per qa_review_target_generalization T7. The qa CLI output target is now
SUBJECT-AWARE:

- ``--subject recon`` (default) → base ``<slug>_qa.md``  (UNCHANGED
  back-compat; the historical path).
- non-recon subject (``qna`` / ``research`` / ``plan``) →
  ``<slug>_qa_<subject>.md`` — each subject gets its own base file, so
  adversarial passes never append-mix across subjects.

Routing tests below pin both halves of that contract end-to-end through
``main()`` (with ``invoke_qa`` stubbed — no LLM).

------------------------------------------------------------------------
OPERATOR-DRIVEN FOLDING — contract contradiction RESOLVED (resolution b)
------------------------------------------------------------------------
A prior pass discovered a contradiction: T7 asserted that qa-on-plan
output (``<slug>_qa_plan.md``) is "operator-folded" (manually fed via
``/plan --reopen``) and NOT auto-ingested, on the premise that the
planner's qa-stem glob already excluded it. That premise was EMPIRICALLY
FALSE — the planner globbed the qa stem with a *wildcard*
(``<slug>_qa_*.md``), which matches ``qa_plan``, so qa-on-plan output
WOULD have been auto-ingested into the planner's ``<qa-findings>`` — the
exact loop the contract meant to avoid.

The OPERATOR chose resolution (b): **number-restrict the planner variant
glob**. ``bin/_planner.main._read_md_group`` now ingests ONLY numeric
follow-up variants ``<slug>_<stem>_<N>.md`` (N = one or more digits) —
the documented "new-file re-run" convention is always numeric
(``_recon_2.md``, ``_3``, …), so a numeric-only filter matches the real
invariant across every stem. Subject-stamped qa outputs
(``<slug>_qa_plan.md`` et al.) carry a non-numeric suffix and are now
deliberately EXCLUDED — they remain operator-folded, never auto-ingested.

This module pins the resolved behavior:

- ``test_planner_qa_glob_excludes_plan_subject_file`` — asserts the
  subject-stamped ``<slug>_qa_plan.md`` is NOT ingested by the qa-stem
  reader (the resolved contract behavior).
- ``test_planner_glob_excludes_plan_subject_file_PER_CONTRACT`` — same
  exclusion assertion; formerly a strict-xfail recording the contract's
  intent, now an ordinary passing test post-resolution.
- ``test_planner_numeric_variant_IS_still_ingested`` — regression guard:
  the number-restriction must NOT break the legitimate numeric new-file
  re-run ingestion (``<slug>_qa_2.md`` / ``<slug>_recon_2.md`` still
  picked up).

The planner-side glob change lives in ``bin/_planner/main.py``; the qa
output routing lives in ``bin/_qa/main.py``.
"""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stderr, redirect_stdout


import bin._qa.main as qa_main
from bin._planner.main import _read_md_group
from bin._qa.exit_codes import EXIT_OK
from bin._qa.invoke import QaResult


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

_STUB_QA_MD = "# qa\nstub-adversarial-pass\n"


def _stub_invoke_qa(*, slug, inputs, chain_id, **_kw):
    """Deterministic stand-in for ``invoke_qa`` — no LLM, no network."""
    return QaResult(
        qa_md=_STUB_QA_MD,
        cost_usd=0.0,
        model_id="stub-model",
        attempt_count=1,
    )


def _make_plan_dir(tmp_path, slug: str, *, subjects=("recon",)):
    """Create a plan dir with the named subject artifacts present.

    Each subject's predecessor artifact ``<slug>_<subject>.md`` must exist
    for the qa predecessor gate to pass.
    """
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)
    for subject in subjects:
        (plan_dir / f"{slug}_{subject}.md").write_text(
            f"# {subject}\nbody-for-{subject}\n", encoding="utf-8"
        )
    return plan_dir


def _run_qa_main(tmp_path, monkeypatch, slug, *, subject):
    """Run ``main(['qa', ...])`` with ``invoke_qa`` stubbed; return exit code."""
    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    monkeypatch.setattr(qa_main, "invoke_qa", _stub_invoke_qa)

    argv = ["qa", slug]
    if subject is not None:
        argv += ["--subject", subject]

    err_buf = io.StringIO()
    out_buf = io.StringIO()
    with redirect_stderr(err_buf), redirect_stdout(out_buf):
        rc = qa_main.main(argv)
    return rc, err_buf.getvalue()


# --------------------------------------------------------------------------- #
# Routing: --subject plan lands at the provenance-distinguishing target        #
# --------------------------------------------------------------------------- #


def test_subject_plan_lands_at_qa_plan_md(tmp_path, monkeypatch):
    """``--subject plan`` writes the review to ``<slug>_qa_plan.md`` (its
    own base file), NOT the recon base ``<slug>_qa.md``."""
    slug = "routeplan"
    plan_dir = _make_plan_dir(tmp_path, slug, subjects=("plan",))

    rc, stderr = _run_qa_main(tmp_path, monkeypatch, slug, subject="plan")
    assert rc == EXIT_OK, f"expected success; got {rc}; stderr={stderr!r}"

    plan_target = plan_dir / f"{slug}_qa_plan.md"
    recon_base = plan_dir / f"{slug}_qa.md"

    assert plan_target.exists(), (
        "qa --subject plan must land at the subject-stamped "
        "<slug>_qa_plan.md"
    )
    assert "stub-adversarial-pass" in plan_target.read_text(encoding="utf-8")
    assert not recon_base.exists(), (
        "qa --subject plan must NOT write the recon base <slug>_qa.md — "
        "subjects own separate files to prevent cross-subject append-mixing"
    )


def test_subject_plan_uses_output_target_helper(tmp_path):
    """The subject-aware base helper resolves plan → ``<slug>_qa_plan.md``
    and recon → ``<slug>_qa.md`` (pins the routing primitive directly)."""
    slug = "routeprim"
    plan_dir = tmp_path / slug
    plan_dir.mkdir()
    assert qa_main._output_target(plan_dir, slug, "plan") == (
        plan_dir / f"{slug}_qa_plan.md"
    )
    assert qa_main._output_target(plan_dir, slug, "recon") == (
        plan_dir / f"{slug}_qa.md"
    )


# --------------------------------------------------------------------------- #
# Back-compat: default (recon) still lands at the base <slug>_qa.md            #
# --------------------------------------------------------------------------- #


def test_default_recon_lands_at_base_qa_md(tmp_path, monkeypatch):
    """A no-``--subject`` (default recon) run still lands at the historical
    base ``<slug>_qa.md`` — back-compat is load-bearing."""
    slug = "routerecon"
    plan_dir = _make_plan_dir(tmp_path, slug, subjects=("recon",))

    rc, stderr = _run_qa_main(tmp_path, monkeypatch, slug, subject=None)
    assert rc == EXIT_OK, f"expected success; got {rc}; stderr={stderr!r}"

    recon_base = plan_dir / f"{slug}_qa.md"
    assert recon_base.exists(), (
        "default-recon run must land at the historical base <slug>_qa.md"
    )
    assert "stub-adversarial-pass" in recon_base.read_text(encoding="utf-8")
    # No subject-stamped recon file should be produced.
    assert not (plan_dir / f"{slug}_qa_recon.md").exists(), (
        "recon must NOT be stamped — its base file is <slug>_qa.md verbatim"
    )


def test_explicit_recon_subject_also_lands_at_base(tmp_path, monkeypatch):
    """An explicit ``--subject recon`` is identical to the default: base
    ``<slug>_qa.md`` (the enum default path is bit-identical to no-flag)."""
    slug = "routerecon2"
    plan_dir = _make_plan_dir(tmp_path, slug, subjects=("recon",))

    rc, stderr = _run_qa_main(tmp_path, monkeypatch, slug, subject="recon")
    assert rc == EXIT_OK, f"expected success; got {rc}; stderr={stderr!r}"
    assert (plan_dir / f"{slug}_qa.md").exists()
    assert not (plan_dir / f"{slug}_qa_recon.md").exists()


def test_recon_and_plan_coexist_in_separate_files(tmp_path, monkeypatch):
    """Running qa for recon AND for plan in the same plan dir yields two
    DISTINCT base files; neither pass is mixed into the other's file."""
    slug = "routeboth"
    plan_dir = _make_plan_dir(tmp_path, slug, subjects=("recon", "plan"))

    rc, _ = _run_qa_main(tmp_path, monkeypatch, slug, subject="recon")
    assert rc == EXIT_OK
    rc, _ = _run_qa_main(tmp_path, monkeypatch, slug, subject="plan")
    assert rc == EXIT_OK

    recon_base = plan_dir / f"{slug}_qa.md"
    plan_base = plan_dir / f"{slug}_qa_plan.md"
    assert recon_base.exists() and plan_base.exists()
    # Each is a standalone pass — neither carries the append separator (no
    # cross-subject stacking).
    assert qa_main._QA_APPEND_SEPARATOR not in recon_base.read_text("utf-8")
    assert qa_main._QA_APPEND_SEPARATOR not in plan_base.read_text("utf-8")


# --------------------------------------------------------------------------- #
# Operator-driven folding — resolved exclusion + numeric-ingest regression     #
#                                                                              #
# Resolution (b): the planner variant glob is number-restricted, so it ingests #
# only numeric new-file re-run variants (<slug>_<stem>_<N>.md) and EXCLUDES     #
# subject-stamped qa output (<slug>_qa_plan.md). The tests below pin both the   #
# exclusion (subject-stamped files stay operator-folded) and the preserved      #
# numeric ingestion (the legitimate new-file re-run path still works). See the  #
# module docstring for the full resolution write-up.                           #
# --------------------------------------------------------------------------- #


def _seed_qa_family(plan_dir, slug):
    """Write a base qa, a numbered qa, and a plan-subject qa file."""
    (plan_dir / f"{slug}_qa.md").write_text("BASE-QA\n", encoding="utf-8")
    (plan_dir / f"{slug}_qa_2.md").write_text("QA-PASS-2\n", encoding="utf-8")
    (plan_dir / f"{slug}_qa_plan.md").write_text(
        "QA-ON-PLAN-CONTENT\n", encoding="utf-8"
    )


def test_planner_qa_glob_excludes_plan_subject_file(tmp_path):
    """RESOLVED BEHAVIOR: the planner's qa-stem reader is number-restricted,
    so it does NOT ingest the subject-stamped ``<slug>_qa_plan.md``.

    Replaces the prior factual pin that asserted the old (buggy) wildcard
    inclusion. Resolution (b) number-restricts the variant glob to
    ``<slug>_<stem>_<N>.md`` — ``qa_plan`` has a non-numeric suffix, so it
    is excluded and remains operator-folded (fed via ``/plan --reopen``),
    never auto-ingested into the planner's ``<qa-findings>``."""
    slug = "folddoc"
    plan_dir = tmp_path / slug
    plan_dir.mkdir()
    _seed_qa_family(plan_dir, slug)

    ingested = _read_md_group(plan_dir, slug, "qa")
    # Base and the NUMERIC variant are still ingested …
    assert "BASE-QA" in ingested
    assert "QA-PASS-2" in ingested
    # … but the subject-stamped qa-on-plan file is excluded.
    assert "QA-ON-PLAN-CONTENT" not in ingested, (
        "RESOLVED: the number-restricted variant glob <slug>_<stem>_<N>.md "
        "must NOT match the non-numeric subject-stamped <slug>_qa_plan.md, "
        "so qa-on-plan output is operator-folded, not auto-ingested"
    )


def test_planner_glob_excludes_plan_subject_file_PER_CONTRACT(tmp_path):
    """CONTRACT BEHAVIOR (now passing): asserts the exclusion the contract
    requested. Formerly a strict-xfail recording the contract's *intent*
    while the wildcard glob still included the file; resolution (b)
    number-restricted the glob, so this is now an ordinary passing test."""
    slug = "foldwant"
    plan_dir = tmp_path / slug
    plan_dir.mkdir()
    _seed_qa_family(plan_dir, slug)

    ingested = _read_md_group(plan_dir, slug, "qa")
    assert "QA-ON-PLAN-CONTENT" not in ingested, (
        "CONTRACT INTENT: qa-on-plan output should be operator-folded, "
        "never auto-ingested by the planner's qa-stem reader"
    )


def test_planner_numeric_variant_IS_still_ingested(tmp_path):
    """REGRESSION GUARD: the number-restriction must NOT break the
    legitimate numeric new-file re-run ingestion.

    Numeric variants ``<slug>_qa_2.md`` and ``<slug>_recon_2.md`` (the
    documented ``_<N>.md`` re-run convention) must still be picked up by
    ``_read_md_group`` alongside their base file — only NON-numeric
    (subject-stamped) suffixes are excluded."""
    slug = "numok"
    plan_dir = tmp_path / slug
    plan_dir.mkdir()

    # qa stem: base + numeric variant + subject-stamped variant.
    (plan_dir / f"{slug}_qa.md").write_text("QA-BASE\n", encoding="utf-8")
    (plan_dir / f"{slug}_qa_2.md").write_text("QA-NUM-2\n", encoding="utf-8")
    (plan_dir / f"{slug}_qa_plan.md").write_text("QA-SUBJECT\n", encoding="utf-8")

    qa_ingested = _read_md_group(plan_dir, slug, "qa")
    assert "QA-BASE" in qa_ingested
    assert "QA-NUM-2" in qa_ingested, (
        "numeric qa variant <slug>_qa_2.md must still be ingested — the "
        "number-restriction must not break the new-file re-run path"
    )
    assert "QA-SUBJECT" not in qa_ingested

    # recon stem: base + numeric variant (no subject-stamping for recon).
    (plan_dir / f"{slug}_recon.md").write_text("RECON-BASE\n", encoding="utf-8")
    (plan_dir / f"{slug}_recon_2.md").write_text("RECON-NUM-2\n", encoding="utf-8")

    recon_ingested = _read_md_group(plan_dir, slug, "recon")
    assert "RECON-BASE" in recon_ingested
    assert "RECON-NUM-2" in recon_ingested, (
        "numeric recon variant <slug>_recon_2.md must still be ingested"
    )
