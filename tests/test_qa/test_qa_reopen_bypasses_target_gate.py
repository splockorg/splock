"""tests/test_qa/test_qa_reopen_bypasses_target_gate.py

Per std_command_operator_extensions TB test_plan #1:

    qa CLI with --reopen and pre-existing <slug>_qa.md succeeds and
    overwrites.

Covers the bypass contract: when ``--reopen`` is set, the target-exists
gate does NOT fire and the CLI proceeds to (mocked) ``invoke_qa`` and
then writes the result, overwriting the previous target contents.

Also covers the no-op variant per the task's spec line (d): ``--reopen``
on a missing target succeeds without raising (writes a fresh first-run
file just like the bare invocation would).

The tests verify content change (rather than mtime alone, which is
sub-millisecond on tmpfs and would be flake-prone). ``invoke_qa`` is
monkeypatched to a stub that returns deterministic MD so the tests do
not call the LLM.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout

import pytest

import bin._qa.main as qa_main
from bin._qa.exit_codes import EXIT_OK
from bin._qa.invoke import QaResult
from bin._qa.main import _build_inputs


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

_STUB_QA_MD = "# qa\nthis-is-the-new-content\n"


def _stub_invoke_qa(*, slug, inputs, chain_id, **_kw):
    return QaResult(
        qa_md=_STUB_QA_MD,
        cost_usd=0.0,
        model_id="stub-model",
        attempt_count=1,
    )


def _make_plan_dir(tmp_path, slug: str):
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)
    # Recon is required to exist for _build_inputs to proceed.
    (plan_dir / f"{slug}_recon.md").write_text(
        "# recon\nsome body\n", encoding="utf-8"
    )
    return plan_dir


def _args_namespace(slug, *, reopen, stdout=False, directive=None):
    import argparse

    return argparse.Namespace(
        slug=slug,
        step="qa",
        repo_state="(no repo-state summary provided)",
        chain_id=None,
        stdout=stdout,
        reopen=reopen,
        directive=directive,
    )


# --------------------------------------------------------------------------- #
# _build_inputs: with --reopen, no _UsageError raised                          #
# --------------------------------------------------------------------------- #


def test_build_inputs_succeeds_when_reopen_set_with_existing_target(tmp_path):
    """qa step + existing qa.md + --reopen → no exception."""
    slug = "reopenbyp_qa"
    plan_dir = _make_plan_dir(tmp_path, slug)
    (plan_dir / f"{slug}_qa.md").write_text(
        "# stale qa\nold body\n", encoding="utf-8"
    )

    args = _args_namespace(slug, reopen=True)
    # Should not raise — bypasses the target-exists gate.
    result = _build_inputs(plan_dir, slug, args)
    assert result is not None


def test_build_inputs_succeeds_when_reopen_set_with_missing_target(tmp_path):
    """qa step + missing qa.md + --reopen → no exception (no-op bypass)."""
    slug = "reopen_missing_qa"
    plan_dir = _make_plan_dir(tmp_path, slug)
    # Deliberately do NOT create qa.md — --reopen on a missing target is a
    # permissive no-op (the bare invocation would succeed too).

    args = _args_namespace(slug, reopen=True)
    result = _build_inputs(plan_dir, slug, args)
    assert result is not None


def test_build_inputs_succeeds_when_no_target_and_no_reopen(tmp_path):
    """qa step + missing qa.md + no --reopen → no exception (clean first run)."""
    slug = "firstrun_qa"
    plan_dir = _make_plan_dir(tmp_path, slug)

    args = _args_namespace(slug, reopen=False)
    result = _build_inputs(plan_dir, slug, args)
    assert result is not None


# --------------------------------------------------------------------------- #
# End-to-end main() invocation: target actually overwritten                    #
# --------------------------------------------------------------------------- #


def test_main_reopen_overwrites_existing_target(tmp_path, monkeypatch):
    """`bin/qa qa --reopen <slug>` against existing qa.md overwrites it.

    Asserts content delta: prior contents replaced by the stub payload.
    """
    slug = "reopenbyp_main_qa"
    plan_dir = _make_plan_dir(tmp_path, slug)
    target = plan_dir / f"{slug}_qa.md"
    target.write_text("# stale qa\nold body\n", encoding="utf-8")
    pre_contents = target.read_text(encoding="utf-8")

    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    monkeypatch.setattr(qa_main, "invoke_qa", _stub_invoke_qa)

    err_buf = io.StringIO()
    out_buf = io.StringIO()
    with redirect_stderr(err_buf), redirect_stdout(out_buf):
        rc = qa_main.main(["qa", "--reopen", slug])
    assert rc == EXIT_OK, (
        f"expected exit 0 (success), got {rc}; stderr={err_buf.getvalue()!r}"
    )

    post_contents = target.read_text(encoding="utf-8")
    assert post_contents != pre_contents, "target contents must have changed"
    assert "this-is-the-new-content" in post_contents, (
        f"post-overwrite contents should reflect the stub payload; got: "
        f"{post_contents!r}"
    )


def test_main_reopen_writes_on_missing_target(tmp_path, monkeypatch):
    """`bin/qa qa --reopen <slug>` on a missing target writes the file
    (no-op bypass — the bare invocation would do the same)."""
    slug = "reopen_missing_main"
    plan_dir = _make_plan_dir(tmp_path, slug)
    target = plan_dir / f"{slug}_qa.md"
    assert not target.exists()

    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    monkeypatch.setattr(qa_main, "invoke_qa", _stub_invoke_qa)

    err_buf = io.StringIO()
    out_buf = io.StringIO()
    with redirect_stderr(err_buf), redirect_stdout(out_buf):
        rc = qa_main.main(["qa", "--reopen", slug])
    assert rc == EXIT_OK
    assert target.exists()
    assert "this-is-the-new-content" in target.read_text(encoding="utf-8")


def test_main_bare_succeeds_on_clean_first_run(tmp_path, monkeypatch):
    """`bin/qa qa <slug>` (no --reopen) on a fresh slug succeeds — the
    target-exists refusal only fires when the target ACTUALLY exists.
    Pins the bare-invocation success path so the refusal contract is not
    accidentally over-broad."""
    slug = "bare_firstrun_qa"
    plan_dir = _make_plan_dir(tmp_path, slug)
    target = plan_dir / f"{slug}_qa.md"
    assert not target.exists()

    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    monkeypatch.setattr(qa_main, "invoke_qa", _stub_invoke_qa)

    err_buf = io.StringIO()
    out_buf = io.StringIO()
    with redirect_stderr(err_buf), redirect_stdout(out_buf):
        rc = qa_main.main(["qa", slug])
    assert rc == EXIT_OK, (
        f"first-run bare qa should succeed; got {rc}; "
        f"stderr={err_buf.getvalue()!r}"
    )
    assert target.exists()


# --------------------------------------------------------------------------- #
# T2 EXTENSION: --reopen vs the subject gate are INDEPENDENT axes               #
#                                                                              #
# The original tests above pin --reopen's bypass of the *target-exists* gate.  #
# T2 generalizes the *predecessor* (subject) gate. These additions assert the  #
# two gates are orthogonal: --reopen never softens the predecessor gate, and   #
# the predecessor gate keys on the chosen subject regardless of --reopen.      #
# --------------------------------------------------------------------------- #

from bin._qa.exit_codes import EXIT_USAGE  # noqa: E402  (extension import)
from bin._qa.main import _UsageError  # noqa: E402
from bin._qa.subject import subject_artifact_name  # noqa: E402


def _subject_args(slug, *, subject, reopen, stdout=False):
    """Namespace carrying an explicit `--subject` (the base `_args_namespace`
    helper above omits it on purpose to exercise the getattr default)."""
    import argparse

    return argparse.Namespace(
        slug=slug,
        step="qa",
        subject=subject,
        repo_state="(no repo-state summary provided)",
        chain_id=None,
        stdout=stdout,
        reopen=reopen,
        new_file=False,
        directive=None,
    )


def test_reopen_does_not_bypass_predecessor_gate(tmp_path):
    """--reopen bypasses the TARGET gate but NOT the predecessor gate: a
    slug whose chosen subject's artifact is missing still refuses even with
    --reopen set. The two gates are independent."""
    slug = "reopen_no_recon"
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)
    # No recon artifact at all; --reopen must NOT paper over the missing
    # predecessor.
    with pytest.raises(_UsageError) as excinfo:
        _build_inputs(plan_dir, slug, _subject_args(slug, subject="recon", reopen=True))
    assert "recon" in str(excinfo.value)


def test_reopen_independent_of_subject_gate_for_nonrecon_subject(tmp_path):
    """--reopen + --subject qna with the qna artifact PRESENT builds inputs
    fine (subject gate satisfied), and the reopen flag does not change the
    predecessor resolution — it only governs the write target."""
    slug = "reopen_qna_present"
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)
    (plan_dir / subject_artifact_name(slug, "qna")).write_text(
        "# qna\nqna-body\n", encoding="utf-8"
    )
    # A stale qa.md exists — the target-exists axis. --reopen will overwrite
    # it; that is orthogonal to the qna predecessor gate, which is satisfied.
    (plan_dir / f"{slug}_qa.md").write_text("# stale\n", encoding="utf-8")

    inputs = _build_inputs(
        plan_dir, slug, _subject_args(slug, subject="qna", reopen=True)
    )
    assert inputs.subject == "qna"
    assert "qna-body" in inputs.subject_findings


def test_main_reopen_with_missing_subject_returns_usage(tmp_path, monkeypatch):
    """End-to-end: --reopen + a missing chosen-subject artifact returns
    EXIT_USAGE (1). --reopen short-circuits ONLY the target-exists refusal,
    never the predecessor refusal."""
    slug = "reopen_missing_subject_e2e"
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)
    # qna chosen but absent; a stale base qa.md present (would be the only
    # thing --reopen cares about).
    (plan_dir / f"{slug}_qa.md").write_text("# stale\n", encoding="utf-8")
    assert not (plan_dir / subject_artifact_name(slug, "qna")).exists()

    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    monkeypatch.setattr(qa_main, "invoke_qa", _stub_invoke_qa)

    err_buf = io.StringIO()
    out_buf = io.StringIO()
    with redirect_stderr(err_buf), redirect_stdout(out_buf):
        rc = qa_main.main(["qa", "--reopen", "--subject", "qna", slug])
    assert rc == EXIT_USAGE, (
        f"--reopen must not bypass the predecessor gate; got {rc}; "
        f"stderr={err_buf.getvalue()!r}"
    )
    assert "qna" in err_buf.getvalue()


def test_main_reopen_overwrites_with_nonrecon_subject(tmp_path, monkeypatch):
    """--reopen + --subject qna with the qna artifact present overwrites the
    QNA-STAMPED base qa file (target-gate bypass intact under a non-default
    subject).

    Updated for qa_review_target_generalization T7: output is now
    subject-aware, so a non-recon subject's base is `<slug>_qa_<subject>.md`
    (here `<slug>_qa_qna.md`), NOT the recon base `<slug>_qa.md`. This test
    pins that --reopen overwrites the *subject's own* base; the orthogonal
    recon-base file is left untouched. (Pre-T7 this asserted overwrite of
    `<slug>_qa.md` because all subjects shared one output file.)"""
    slug = "reopen_qna_overwrite_e2e"
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)
    (plan_dir / subject_artifact_name(slug, "qna")).write_text(
        "# qna\nqna-body\n", encoding="utf-8"
    )
    # The qna subject's OWN base file (the T7 routing target).
    qna_target = plan_dir / f"{slug}_qa_qna.md"
    qna_target.write_text("# stale qna qa\nold body\n", encoding="utf-8")
    # An unrelated recon base — must be left untouched by a qna-subject run.
    recon_base = plan_dir / f"{slug}_qa.md"
    recon_base.write_text("# recon qa\nrecon body\n", encoding="utf-8")

    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    monkeypatch.setattr(qa_main, "invoke_qa", _stub_invoke_qa)

    err_buf = io.StringIO()
    out_buf = io.StringIO()
    with redirect_stderr(err_buf), redirect_stdout(out_buf):
        rc = qa_main.main(["qa", "--reopen", "--subject", "qna", slug])
    assert rc == EXIT_OK, (
        f"--reopen + present qna should overwrite; got {rc}; "
        f"stderr={err_buf.getvalue()!r}"
    )
    # The qna-stamped base was overwritten with the stub payload …
    assert "this-is-the-new-content" in qna_target.read_text(encoding="utf-8")
    # … while the recon base is untouched (subjects own separate files).
    assert recon_base.read_text(encoding="utf-8") == "# recon qa\nrecon body\n"
