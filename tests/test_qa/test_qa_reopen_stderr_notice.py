"""tests/test_qa/test_qa_reopen_stderr_notice.py

Per std_command_operator_extensions TB test_plan #3:

    Stderr notice matches TA contract on overwrite.

Covers the destructive-op signal contract: when ``--reopen`` causes a
real overwrite (target existed pre-run AND was just written), the CLI
emits ``Reopened: overwrote <abs-path>`` to stderr so the operator and
any log-watcher sees that a destructive action was taken intentionally.

The notice MUST NOT fire on a clean first-run write (target did NOT
exist pre-run) — that would generate false-positive destructive-op
signals for normal first authorship. This invariant holds even when
``--reopen`` is set (the flag is permissive, not indicative).

The absolute path is included so the operator can recover the prior
contents from a backup if needed without ambiguity about which file.

The notice format mirrors ``bin._planner.main``'s TA stderr notice
byte-for-byte (``Reopened: overwrote <abs-path>``) for cross-CLI
consistency.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout

import pytest

import bin._qa.main as qa_main
from bin._qa.exit_codes import EXIT_OK
from bin._qa.invoke import QaResult


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

_STUB_QA_MD = "# qa\nstderr-notice-test\n"


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
    (plan_dir / f"{slug}_recon.md").write_text(
        "# recon\nsome body\n", encoding="utf-8"
    )
    return plan_dir


# --------------------------------------------------------------------------- #
# Notice FIRES on actual overwrite                                            #
# --------------------------------------------------------------------------- #


def test_stderr_notice_fires_on_actual_qa_overwrite(tmp_path, monkeypatch):
    """`bin/qa qa --reopen <slug>` against pre-existing qa.md: stderr
    contains `Reopened: overwrote <abs-path>` with the absolute path."""
    slug = "stderr_qa"
    plan_dir = _make_plan_dir(tmp_path, slug)
    target = plan_dir / f"{slug}_qa.md"
    target.write_text("# stale\n", encoding="utf-8")

    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    monkeypatch.setattr(qa_main, "invoke_qa", _stub_invoke_qa)

    err_buf = io.StringIO()
    out_buf = io.StringIO()
    with redirect_stderr(err_buf), redirect_stdout(out_buf):
        rc = qa_main.main(["qa", "--reopen", slug])
    assert rc == EXIT_OK, (
        f"expected exit 0; got {rc}; stderr={err_buf.getvalue()!r}"
    )

    stderr_text = err_buf.getvalue()
    expected_substring = f"Reopened: overwrote {target.resolve()}"
    assert expected_substring in stderr_text, (
        f"expected stderr to contain {expected_substring!r}; "
        f"got: {stderr_text!r}"
    )


def test_stderr_notice_uses_absolute_path(tmp_path, monkeypatch):
    """The notice must include an ABSOLUTE path (not relative) so the
    operator's log-search/backup recovery is unambiguous. Pin via
    ``Path.is_absolute()`` substring extraction."""
    slug = "stderr_abspath"
    plan_dir = _make_plan_dir(tmp_path, slug)
    target = plan_dir / f"{slug}_qa.md"
    target.write_text("# stale\n", encoding="utf-8")

    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    monkeypatch.setattr(qa_main, "invoke_qa", _stub_invoke_qa)

    err_buf = io.StringIO()
    out_buf = io.StringIO()
    with redirect_stderr(err_buf), redirect_stdout(out_buf):
        rc = qa_main.main(["qa", "--reopen", slug])
    assert rc == EXIT_OK

    stderr_text = err_buf.getvalue()
    # Find the "Reopened: overwrote " prefix and extract the path portion.
    prefix = "Reopened: overwrote "
    assert prefix in stderr_text
    # Extract the path on that line.
    for line in stderr_text.splitlines():
        if line.startswith(prefix):
            path_str = line[len(prefix):].strip()
            from pathlib import Path
            assert Path(path_str).is_absolute(), (
                f"notice path must be absolute; got: {path_str!r}"
            )
            break
    else:
        pytest.fail(f"no Reopened: line in stderr: {stderr_text!r}")


# --------------------------------------------------------------------------- #
# Notice DOES NOT FIRE on clean first-run write                                #
# --------------------------------------------------------------------------- #


def test_stderr_notice_silent_on_first_run_bare_invocation(
    tmp_path, monkeypatch
):
    """`bin/qa qa <slug>` (no --reopen, no pre-existing target): stderr
    must NOT contain the `Reopened:` notice. First-run authorship is not
    a destructive op."""
    slug = "stderr_firstrun_bare"
    plan_dir = _make_plan_dir(tmp_path, slug)
    # NO pre-existing target — this is a clean first run.

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

    stderr_text = err_buf.getvalue()
    assert "Reopened:" not in stderr_text, (
        f"stderr must NOT contain 'Reopened:' on a first-run write; got: "
        f"{stderr_text!r}"
    )


def test_stderr_notice_silent_on_first_run_even_with_reopen_flag(
    tmp_path, monkeypatch
):
    """Edge case: `bin/qa qa --reopen <slug>` with NO pre-existing target
    must also be silent on the notice — `--reopen` is permissive, not
    indicative of an overwrite. Only the actual pre-existence + write
    combo earns the notice.

    This is the TB-specific invariant called out in the task prompt:
    "silent on clean first-run write even when --reopen set."
    """
    slug = "stderr_firstrun_with_reopen"
    plan_dir = _make_plan_dir(tmp_path, slug)
    # NO pre-existing target despite --reopen being set.

    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    monkeypatch.setattr(qa_main, "invoke_qa", _stub_invoke_qa)

    err_buf = io.StringIO()
    out_buf = io.StringIO()
    with redirect_stderr(err_buf), redirect_stdout(out_buf):
        rc = qa_main.main(["qa", "--reopen", slug])
    assert rc == EXIT_OK

    stderr_text = err_buf.getvalue()
    assert "Reopened:" not in stderr_text, (
        "stderr must NOT contain 'Reopened:' when --reopen is set but no "
        "overwrite actually occurred (target did not pre-exist)"
    )
