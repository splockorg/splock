"""qa CLI backward-compat — no-flag invocation resolves the recon path.

Per qa_review_target_generalization T1. Backward-compat is load-bearing:
introducing the `--subject` enum MUST NOT change what a no-`--subject`
invocation resolves. A bare `bin/qa qa <slug>` must read the exact
`<slug>_recon.md` path the CLI read before the enum existed, and pack
`subject == 'recon'`.

These tests pin:

1. The default subject (DEFAULT_SUBJECT) is `recon`.
2. A namespace built WITHOUT a `subject` attribute (a programmatic caller
   from before the flag existed) still resolves recon via the defensive
   `getattr` default in `_build_inputs` — no AttributeError regression.
3. With recon present, `_build_inputs` reads `<slug>_recon.md` exactly and
   the resulting QaInputs.subject is `recon`.
4. End-to-end through `main(['qa', slug])` (no --subject), the recon file
   is the artifact consumed — proven by feeding a non-recon artifact a
   *different* body and confirming the recon body is what flows through.
"""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stderr, redirect_stdout

import pytest

import bin._qa.main as qa_main
from bin._qa.exit_codes import EXIT_OK, EXIT_USAGE
from bin._qa.invoke import QaResult
from bin._qa.main import _build_inputs
from bin._qa.subject import DEFAULT_SUBJECT


_RECON_BODY = "# recon\nthe-historical-recon-body\n"


def _make_plan_dir(tmp_path, slug: str):
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)
    (plan_dir / f"{slug}_recon.md").write_text(_RECON_BODY, encoding="utf-8")
    return plan_dir


def _stub_invoke_qa(captured):
    def _inner(*, slug, inputs, chain_id, **_kw):
        captured["inputs"] = inputs
        return QaResult(
            qa_md="# qa\nstub body\n",
            cost_usd=0.0,
            model_id="stub-model",
            attempt_count=1,
        )

    return _inner


# ----------------------------------------------------------------------
# Default = recon
# ----------------------------------------------------------------------

def test_default_subject_constant_is_recon() -> None:
    """The module-level default subject is `recon` (preserves history)."""
    assert DEFAULT_SUBJECT == "recon"


def test_build_inputs_no_subject_attr_resolves_recon(tmp_path) -> None:
    """A Namespace built WITHOUT a `subject` attribute (pre-flag
    programmatic caller) still resolves the recon artifact via the
    defensive getattr default — no AttributeError, no behavior change."""
    slug = "compat_no_attr"
    plan_dir = _make_plan_dir(tmp_path, slug)

    # Deliberately omit `subject` to mimic a caller from before the flag.
    args = argparse.Namespace(
        slug=slug,
        step="qa",
        repo_state="(no repo-state summary provided)",
        chain_id=None,
        stdout=False,
        reopen=False,
        new_file=False,
        directive=None,
    )
    result = _build_inputs(plan_dir, slug, args)
    assert result.subject == "recon"
    assert result.subject_findings == _RECON_BODY


def test_build_inputs_default_reads_exact_recon_path(tmp_path) -> None:
    """With `--subject` defaulted to recon, `_build_inputs` reads exactly
    `<slug>_recon.md` and packs subject == 'recon'."""
    slug = "compat_recon_path"
    plan_dir = _make_plan_dir(tmp_path, slug)

    args = argparse.Namespace(
        slug=slug,
        step="qa",
        subject="recon",
        repo_state="(no repo-state summary provided)",
        chain_id=None,
        stdout=False,
        reopen=False,
        new_file=False,
        directive=None,
    )
    result = _build_inputs(plan_dir, slug, args)
    assert result.subject == "recon"
    assert result.subject_findings == _RECON_BODY


def test_build_inputs_missing_recon_still_refuses(tmp_path) -> None:
    """Backward-compat of the predecessor gate: with the default subject
    and no recon present, `_build_inputs` still refuses (raises). The gate
    did not loosen for the default subject."""
    slug = "compat_missing_recon"
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)
    # No recon written.

    args = argparse.Namespace(
        slug=slug,
        step="qa",
        subject="recon",
        repo_state="(no repo-state summary provided)",
        chain_id=None,
        stdout=False,
        reopen=False,
        new_file=False,
        directive=None,
    )
    with pytest.raises(Exception):  # _UsageError subclasses ValueError
        _build_inputs(plan_dir, slug, args)


# ----------------------------------------------------------------------
# End-to-end: bare main() consumes the recon artifact
# ----------------------------------------------------------------------

def test_main_bare_consumes_recon_artifact(tmp_path, monkeypatch) -> None:
    """`main(['qa', slug])` (no --subject) feeds the recon body to the
    (stubbed) invoke_qa with subject == 'recon'. A differently-bodied
    non-recon artifact present in the dir is NOT what flows through."""
    slug = "compat_e2e"
    plan_dir = _make_plan_dir(tmp_path, slug)
    # A qna artifact with a DIFFERENT body — must be ignored by the default.
    (plan_dir / f"{slug}_qna.md").write_text(
        "# qna\nNOT-the-recon-body\n", encoding="utf-8"
    )

    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    captured: dict = {}
    monkeypatch.setattr(qa_main, "invoke_qa", _stub_invoke_qa(captured))

    err_buf = io.StringIO()
    out_buf = io.StringIO()
    with redirect_stderr(err_buf), redirect_stdout(out_buf):
        rc = qa_main.main(["qa", slug, "--stdout"])

    assert rc == EXIT_OK, (
        f"bare default-recon invocation should succeed; got {rc}, "
        f"stderr={err_buf.getvalue()!r}"
    )
    assert captured["inputs"].subject == "recon"
    assert captured["inputs"].subject_findings == _RECON_BODY
    assert "NOT-the-recon-body" not in captured["inputs"].subject_findings


def test_main_default_with_no_recon_refuses_exit_usage(tmp_path, monkeypatch) -> None:
    """`main(['qa', slug])` (no --subject, no recon) returns EXIT_USAGE
    (1) — the predecessor gate's backward-compat refusal is intact."""
    slug = "compat_e2e_no_recon"
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)

    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)

    err_buf = io.StringIO()
    out_buf = io.StringIO()
    with redirect_stderr(err_buf), redirect_stdout(out_buf):
        rc = qa_main.main(["qa", slug, "--stdout"])

    assert rc == EXIT_USAGE, (
        f"missing recon under default subject must refuse with EXIT_USAGE "
        f"({EXIT_USAGE}); got {rc}"
    )
