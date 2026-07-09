"""tests/test_qa/test_qa_no_recon_gate_for_nonrecon_subjects.py

Per qa_review_target_generalization T2 — the LOAD-BEARING over-gating
guard.

Before generalization the gate keyed on `<slug>_recon.md`. The risk in
generalizing is a residual recon-coupling: a `--subject qna` (or
research / plan) run that still refuses merely because a recon artifact
is ABSENT would defeat the whole feature — a slug that never ran recon
but has a qna / research / plan artifact must sail past the gate.

These tests pin that the gate depends ONLY on the chosen subject's
artifact, NEVER on `_recon.md`:

- a recon-LESS slug (no `<slug>_recon.md` on disk at all) with a present
  `<slug>_qna.md` / `_research.md` / `_plan.md` SUCCEEDS past the gate
  for `--subject qna|research|plan`,
- and as a paired negative, that same recon-less slug STILL refuses if
  the chosen subject's OWN artifact is the one missing — proving the gate
  moved to the subject, it did not simply go away.

Tested in-process; `_PLANS_DIR` monkeypatched to `tmp_path`, `invoke_qa`
stubbed.
"""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stderr, redirect_stdout

import pytest

import bin._qa.main as qa_main
from bin._qa.exit_codes import EXIT_OK, EXIT_USAGE
from bin._qa.invoke import QaResult
from bin._qa.main import _UsageError, _build_inputs
from bin._qa.subject import subject_artifact_name


# Every subject EXCEPT recon — recon's own gate is covered elsewhere; the
# over-gating risk is specifically that a NON-recon subject stays coupled
# to recon's presence.
_NON_RECON_SUBJECTS = ("qna", "research", "plan")


def _args(slug: str, subject: str, *, stdout=False):
    return argparse.Namespace(
        slug=slug,
        step="qa",
        subject=subject,
        repo_state="(no repo-state summary provided)",
        chain_id=None,
        stdout=stdout,
        reopen=False,
        new_file=False,
        directive=None,
    )


def _stub_invoke_qa(captured):
    def _inner(*, slug, inputs, chain_id, **_kw):
        captured["inputs"] = inputs
        return QaResult(
            qa_md="# qa\nstub\n",
            cost_usd=0.0,
            model_id="stub-model",
            attempt_count=1,
        )

    return _inner


# --------------------------------------------------------------------------- #
# A recon-LESS slug with the chosen non-recon artifact present SUCCEEDS         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("subject", _NON_RECON_SUBJECTS)
def test_build_inputs_passes_without_recon_when_subject_present(tmp_path, subject):
    """No `<slug>_recon.md` on disk; the chosen non-recon artifact present
    → `_build_inputs` does NOT raise. The gate must not be recon-coupled."""
    slug = f"norecon_ok_{subject}"
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)

    # Confirm the recon artifact genuinely does NOT exist for this slug.
    assert not (plan_dir / subject_artifact_name(slug, "recon")).exists()

    # Only the chosen non-recon subject's artifact is present.
    (plan_dir / subject_artifact_name(slug, subject)).write_text(
        f"# {subject}\nthe-{subject}-body\n", encoding="utf-8"
    )

    inputs = _build_inputs(plan_dir, slug, _args(slug, subject))
    assert inputs.subject == subject
    assert f"the-{subject}-body" in inputs.subject_findings


@pytest.mark.parametrize("subject", _NON_RECON_SUBJECTS)
def test_main_passes_without_recon_when_subject_present(
    tmp_path, monkeypatch, subject
):
    """End-to-end: recon-less slug + present non-recon artifact →
    EXIT_OK, and the chosen artifact's body is what flows to invoke_qa."""
    slug = f"norecon_e2e_ok_{subject}"
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)
    assert not (plan_dir / subject_artifact_name(slug, "recon")).exists()

    body = f"# {subject}\nbody-of-{subject}\n"
    (plan_dir / subject_artifact_name(slug, subject)).write_text(
        body, encoding="utf-8"
    )

    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    captured: dict = {}
    monkeypatch.setattr(qa_main, "invoke_qa", _stub_invoke_qa(captured))

    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        rc = qa_main.main(["qa", slug, "--subject", subject, "--stdout"])

    assert rc == EXIT_OK, (
        f"recon-less slug with present {subject} artifact must pass the "
        f"gate; got {rc}; stderr={err.getvalue()!r}"
    )
    assert captured["inputs"].subject == subject
    assert captured["inputs"].subject_findings == body


# --------------------------------------------------------------------------- #
# Paired negative: the gate MOVED to the subject — it did not vanish           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("subject", _NON_RECON_SUBJECTS)
def test_build_inputs_still_refuses_when_chosen_subject_missing(tmp_path, subject):
    """Recon-less slug; the chosen subject's OWN artifact also missing →
    still refuses (names the chosen subject, not recon). Proves the gate
    relocated to the subject rather than being removed entirely."""
    slug = f"norecon_refuse_{subject}"
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)
    # Nothing written: neither recon nor the chosen subject's artifact.

    with pytest.raises(_UsageError) as excinfo:
        _build_inputs(plan_dir, slug, _args(slug, subject))

    msg = str(excinfo.value)
    assert subject in msg
    assert "recon" not in msg.replace(slug, ""), (
        f"a {subject} refusal must not name recon; got: {msg!r}"
    )


@pytest.mark.parametrize("subject", _NON_RECON_SUBJECTS)
def test_main_refuses_when_chosen_subject_missing_even_with_recon_present(
    tmp_path, monkeypatch, subject
):
    """Inverse coupling check: a recon artifact PRESENT must NOT satisfy a
    non-recon subject. `--subject qna` with only `_recon.md` on disk still
    refuses with EXIT_USAGE — the gate keys on the chosen subject alone."""
    slug = f"recon_present_subject_missing_{subject}"
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)
    # Recon IS present...
    (plan_dir / subject_artifact_name(slug, "recon")).write_text(
        "# recon\npresent\n", encoding="utf-8"
    )
    # ...but the chosen subject's artifact is NOT.
    assert not (plan_dir / subject_artifact_name(slug, subject)).exists()

    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    monkeypatch.setattr(qa_main, "invoke_qa", _stub_invoke_qa({}))

    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        rc = qa_main.main(["qa", slug, "--subject", subject, "--stdout"])

    assert rc == EXIT_USAGE, (
        f"recon present but {subject} missing must still refuse "
        f"(gate keys on the chosen subject); got {rc}"
    )
    assert subject in err.getvalue()
