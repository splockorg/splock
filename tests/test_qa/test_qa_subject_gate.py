"""tests/test_qa/test_qa_subject_gate.py

Per qa_review_target_generalization T2: the predecessor gate is
generalized from recon-only to the chosen `--subject`. For EVERY subject
(recon / qna / research / plan) the gate must:

- REFUSE (exit 1 / EXIT_USAGE) when `<slug>_<subject>.md` is MISSING,
- REFUSE (exit 1 / EXIT_USAGE) when `<slug>_<subject>.md` is EMPTY
  (whitespace-only counts as empty),
- SUCCEED past the gate when `<slug>_<subject>.md` is present + non-empty.

The refusal is EXIT_USAGE (1) — NOT a new code — and the closed
`ALL_CODES` frozenset is unchanged by this task (exit_codes.py is not
touched; EXIT_USAGE already covers a missing/empty predecessor).

The refusal MESSAGE names the chosen subject (T2's load-bearing wording
change): a `--subject qna` refusal must say "qna", not "recon".

Tested in-process; `_PLANS_DIR` is monkeypatched to `tmp_path` and the
SDK call (`invoke_qa`) is monkeypatched so no model is hit on the
success path.
"""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stderr, redirect_stdout

import pytest

import bin._qa.main as qa_main
from bin._qa.exit_codes import ALL_CODES, EXIT_OK, EXIT_USAGE
from bin._qa.invoke import QaResult
from bin._qa.main import _UsageError, _build_inputs
from bin._qa.subject import SUBJECT_CHOICES, subject_artifact_name


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _write_subject(plan_dir, slug: str, subject: str, body: str = "# body\nx\n"):
    """Write the `<slug>_<subject>.md` artifact for `subject`."""
    (plan_dir / subject_artifact_name(slug, subject)).write_text(
        body, encoding="utf-8"
    )


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


def _stub_invoke_qa(*, slug, inputs, chain_id, **_kw):
    return QaResult(
        qa_md="# qa\nstub\n",
        cost_usd=0.0,
        model_id="stub-model",
        attempt_count=1,
    )


# --------------------------------------------------------------------------- #
# ALL_CODES frozenset is unchanged (exit_codes.py not touched by T2)           #
# --------------------------------------------------------------------------- #


def test_all_codes_frozenset_unchanged():
    """T2 adds NO new exit code. The closed `ALL_CODES` set stays exactly
    {0, 1, 7, 8, 17}; the predecessor refusal reuses EXIT_USAGE (1)."""
    assert ALL_CODES == frozenset({0, 1, 7, 8, 17})
    # And the refusal code we rely on is the generic usage error, 1.
    assert EXIT_USAGE == 1
    assert EXIT_USAGE in ALL_CODES


# --------------------------------------------------------------------------- #
# Missing artifact → refuse, for EVERY subject                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("subject", SUBJECT_CHOICES)
def test_build_inputs_refuses_when_subject_missing(tmp_path, subject):
    """For every subject, a missing `<slug>_<subject>.md` raises
    _UsageError (→ EXIT_USAGE) and the message names the chosen subject."""
    slug = f"gate_missing_{subject}"
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)
    # Deliberately write NOTHING — the chosen subject's artifact is absent.

    with pytest.raises(_UsageError) as excinfo:
        _build_inputs(plan_dir, slug, _args(slug, subject))

    msg = str(excinfo.value)
    assert subject in msg, (
        f"refusal must name the chosen subject {subject!r}; got: {msg!r}"
    )
    assert "does not exist" in msg
    # The resolved subject path is interpolated (T1 behavior preserved).
    assert subject_artifact_name(slug, subject) in msg


@pytest.mark.parametrize("subject", SUBJECT_CHOICES)
def test_build_inputs_refuses_when_subject_empty(tmp_path, subject):
    """For every subject, a whitespace-only `<slug>_<subject>.md` raises
    _UsageError, and the message names the subject + says 'empty'."""
    slug = f"gate_empty_{subject}"
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)
    _write_subject(plan_dir, slug, subject, body="   \n\t\n")

    with pytest.raises(_UsageError) as excinfo:
        _build_inputs(plan_dir, slug, _args(slug, subject))

    msg = str(excinfo.value)
    assert subject in msg, (
        f"empty-artifact refusal must name {subject!r}; got: {msg!r}"
    )
    assert "empty" in msg


@pytest.mark.parametrize("subject", SUBJECT_CHOICES)
def test_build_inputs_succeeds_when_subject_present(tmp_path, subject):
    """For every subject, a present non-empty artifact builds inputs
    without raising; the chosen subject is packed onto QaInputs and the
    body flows into subject_findings (the subject-agnostic content field)."""
    slug = f"gate_present_{subject}"
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)
    body = f"# {subject}\nthe-{subject}-body\n"
    _write_subject(plan_dir, slug, subject, body=body)

    inputs = _build_inputs(plan_dir, slug, _args(slug, subject))
    assert inputs.subject == subject
    assert inputs.subject_findings == body


# --------------------------------------------------------------------------- #
# End-to-end main() — refusal returns EXIT_USAGE (1), per subject               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("subject", SUBJECT_CHOICES)
def test_main_missing_subject_returns_exit_usage(tmp_path, monkeypatch, subject):
    """`main(['qa', slug, '--subject', subject])` on a missing artifact
    returns EXIT_USAGE (1) — NOT a bespoke code — for every subject."""
    slug = f"gate_e2e_missing_{subject}"
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)

    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    monkeypatch.setattr(qa_main, "invoke_qa", _stub_invoke_qa)

    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        rc = qa_main.main(["qa", slug, "--subject", subject, "--stdout"])

    assert rc == EXIT_USAGE, (
        f"missing {subject} artifact must refuse with EXIT_USAGE "
        f"({EXIT_USAGE}); got {rc}"
    )
    # Refusal envelope names the subject too.
    assert subject in err.getvalue()


@pytest.mark.parametrize("subject", SUBJECT_CHOICES)
def test_main_present_subject_succeeds(tmp_path, monkeypatch, subject):
    """`main(['qa', slug, '--subject', subject])` with the artifact present
    succeeds (EXIT_OK) for every subject — proves the gate passes through
    when the chosen predecessor exists."""
    slug = f"gate_e2e_present_{subject}"
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)
    _write_subject(plan_dir, slug, subject, body=f"# {subject}\nbody\n")

    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    monkeypatch.setattr(qa_main, "invoke_qa", _stub_invoke_qa)

    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        rc = qa_main.main(["qa", slug, "--subject", subject, "--stdout"])

    assert rc == EXIT_OK, (
        f"present {subject} artifact should pass the gate; got {rc}; "
        f"stderr={err.getvalue()!r}"
    )
