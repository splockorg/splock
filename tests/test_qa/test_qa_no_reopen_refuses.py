"""tests/test_qa/test_qa_no_reopen_refuses.py

Re-run-mode contract for `bin/qa` (supersedes the old "no --reopen →
refuse" contract).

Historical note: this module formerly asserted that running ``bin/qa qa
<slug>`` without ``--reopen`` against an existing ``<slug>_qa.md`` refused
with exit code 8. That hard refusal was removed when the exploratory
commands gained natural-language re-run modes — qa now:

- APPENDS by default (a new adversarial pass is appended to the existing
  ``<slug>_qa.md`` under a provenance separator),
- writes the next free ``<slug>_qa_<N>.md`` with ``--new-file``,
- overwrites the base in place with ``--reopen``,
- treats ``--reopen`` + ``--new-file`` together as a usage error.

The exit-code-8 constant (`EXIT_TARGET_EXISTS_NO_REOPEN`) is RETAINED in
the closed enum for cross-CLI parity with the planner (so a chain-driver
examining ``$?`` interprets the planner's overwrite-refusal uniformly),
even though qa no longer raises it. The two enum-pin tests at the bottom
still guard that constant.

Tested in-process; ``_PLANS_DIR`` is monkeypatched to ``tmp_path`` and
the SDK call (``invoke_qa``) is monkeypatched so no model is hit.
"""

from __future__ import annotations

import argparse
import io
import types
from contextlib import redirect_stderr, redirect_stdout

import pytest

import bin._qa.main as qa_main
from bin._qa.exit_codes import EXIT_OK, EXIT_USAGE
from bin._qa.main import (
    _build_inputs,
    _next_numbered_target,
    _resolve_rerun_mode,
    _resolve_target_and_body,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _make_plan_dir(tmp_path, slug: str):
    plan_dir = tmp_path / slug
    plan_dir.mkdir(parents=True)
    (plan_dir / f"{slug}_recon.md").write_text(
        "# recon\nsome body\n", encoding="utf-8"
    )
    return plan_dir


def _args(slug: str, *, reopen=False, new_file=False, stdout=False):
    return argparse.Namespace(
        slug=slug,
        step="qa",
        repo_state="(no repo-state summary provided)",
        chain_id=None,
        stdout=stdout,
        reopen=reopen,
        new_file=new_file,
        directive=None,
    )


def _patch_sdk(monkeypatch, qa_md: str = "NEW PASS BODY") -> None:
    """Replace the SDK call so main() exercises only the write path."""
    monkeypatch.setattr(
        qa_main,
        "invoke_qa",
        lambda **kwargs: types.SimpleNamespace(qa_md=qa_md),
    )


# --------------------------------------------------------------------------- #
# _resolve_rerun_mode                                                          #
# --------------------------------------------------------------------------- #


def test_default_mode_is_append():
    assert _resolve_rerun_mode(_args("s")) == "append"


def test_reopen_mode_is_overwrite():
    assert _resolve_rerun_mode(_args("s", reopen=True)) == "overwrite"


def test_new_file_mode():
    assert _resolve_rerun_mode(_args("s", new_file=True)) == "new-file"


def test_both_flags_is_usage_error():
    with pytest.raises(qa_main._UsageError):
        _resolve_rerun_mode(_args("s", reopen=True, new_file=True))


# --------------------------------------------------------------------------- #
# _resolve_target_and_body                                                     #
# --------------------------------------------------------------------------- #


def test_first_authorship_writes_base_regardless_of_mode(tmp_path):
    slug = "fresh"
    plan_dir = tmp_path / slug
    plan_dir.mkdir()
    for mode in ("append", "new-file", "overwrite"):
        target, body = _resolve_target_and_body(plan_dir, slug, mode, "BODY")
        assert target == plan_dir / f"{slug}_qa.md"
        assert body == "BODY"


def test_append_stacks_existing_then_separator_then_new(tmp_path):
    slug = "appendcase"
    plan_dir = tmp_path / slug
    plan_dir.mkdir()
    base = plan_dir / f"{slug}_qa.md"
    base.write_text("OLD REVIEW", encoding="utf-8")

    target, body = _resolve_target_and_body(plan_dir, slug, "append", "NEW REVIEW")
    assert target == base
    assert body.startswith("OLD REVIEW")
    assert "NEW REVIEW" in body
    assert qa_main._QA_APPEND_SEPARATOR in body
    # OLD precedes the separator precedes NEW
    assert body.index("OLD REVIEW") < body.index(
        qa_main._QA_APPEND_SEPARATOR
    ) < body.index("NEW REVIEW")


def test_overwrite_replaces_base(tmp_path):
    slug = "ovr"
    plan_dir = tmp_path / slug
    plan_dir.mkdir()
    base = plan_dir / f"{slug}_qa.md"
    base.write_text("OLD", encoding="utf-8")

    target, body = _resolve_target_and_body(plan_dir, slug, "overwrite", "NEW")
    assert target == base
    assert body == "NEW"


def test_new_file_picks_next_free_numbered_target(tmp_path):
    slug = "nf"
    plan_dir = tmp_path / slug
    plan_dir.mkdir()
    (plan_dir / f"{slug}_qa.md").write_text("BASE", encoding="utf-8")

    target, body = _resolve_target_and_body(plan_dir, slug, "new-file", "NEW")
    assert target == plan_dir / f"{slug}_qa_2.md"
    assert body == "NEW"

    # With qa_2 present, the next new-file lands at qa_3.
    target.write_text("NEW", encoding="utf-8")
    target3, _ = _resolve_target_and_body(plan_dir, slug, "new-file", "NEW3")
    assert target3 == plan_dir / f"{slug}_qa_3.md"


def test_next_numbered_target_starts_at_two(tmp_path):
    slug = "n2"
    plan_dir = tmp_path / slug
    plan_dir.mkdir()
    assert _next_numbered_target(plan_dir, slug) == plan_dir / f"{slug}_qa_2.md"


# --------------------------------------------------------------------------- #
# _build_inputs no longer refuses on an existing target                        #
# --------------------------------------------------------------------------- #


def test_build_inputs_does_not_refuse_when_target_exists(tmp_path):
    """The old _TargetExistsNoReopenError path is gone: an existing qa.md
    with no --reopen must NOT raise — it builds inputs normally."""
    slug = "noraise"
    plan_dir = _make_plan_dir(tmp_path, slug)
    (plan_dir / f"{slug}_qa.md").write_text("# stale\n", encoding="utf-8")

    inputs = _build_inputs(plan_dir, slug, _args(slug, reopen=False))
    assert inputs.subject_findings  # subject artifact was read; no exception


def test_build_inputs_still_refuses_when_recon_missing(tmp_path):
    """The predecessor gate stays: missing recon → usage error."""
    slug = "norecon"
    plan_dir = tmp_path / slug
    plan_dir.mkdir()
    with pytest.raises(qa_main._UsageError):
        _build_inputs(plan_dir, slug, _args(slug))


# --------------------------------------------------------------------------- #
# End-to-end main() — mode wiring (SDK monkeypatched)                          #
# --------------------------------------------------------------------------- #


def test_main_append_default_stacks_into_base(tmp_path, monkeypatch):
    slug = "e2e_append"
    plan_dir = _make_plan_dir(tmp_path, slug)
    (plan_dir / f"{slug}_qa.md").write_text("OLD", encoding="utf-8")
    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    _patch_sdk(monkeypatch, "FRESH PASS")

    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        rc = qa_main.main(["qa", slug])

    assert rc == EXIT_OK
    body = (plan_dir / f"{slug}_qa.md").read_text(encoding="utf-8")
    assert "OLD" in body and "FRESH PASS" in body
    assert "Appended" in err.getvalue()
    # No stray numbered file was created.
    assert not (plan_dir / f"{slug}_qa_2.md").exists()


def test_main_new_file_writes_numbered_and_leaves_base(tmp_path, monkeypatch):
    slug = "e2e_newfile"
    plan_dir = _make_plan_dir(tmp_path, slug)
    (plan_dir / f"{slug}_qa.md").write_text("OLD", encoding="utf-8")
    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    _patch_sdk(monkeypatch, "SECOND PASS")

    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        rc = qa_main.main(["qa", slug, "--new-file"])

    assert rc == EXIT_OK
    assert (plan_dir / f"{slug}_qa.md").read_text(encoding="utf-8") == "OLD"
    assert (plan_dir / f"{slug}_qa_2.md").read_text(
        encoding="utf-8"
    ).rstrip("\n") == "SECOND PASS"
    assert "New file" in err.getvalue()


def test_main_reopen_overwrites_base(tmp_path, monkeypatch):
    slug = "e2e_reopen"
    plan_dir = _make_plan_dir(tmp_path, slug)
    (plan_dir / f"{slug}_qa.md").write_text("OLD", encoding="utf-8")
    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    _patch_sdk(monkeypatch, "REPLACED")

    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        rc = qa_main.main(["qa", slug, "--reopen"])

    assert rc == EXIT_OK
    assert (plan_dir / f"{slug}_qa.md").read_text(
        encoding="utf-8"
    ).rstrip("\n") == "REPLACED"
    assert "Reopened" in err.getvalue()


def test_main_both_flags_returns_usage(tmp_path, monkeypatch):
    slug = "e2e_both"
    _make_plan_dir(tmp_path, slug)
    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    _patch_sdk(monkeypatch)

    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        rc = qa_main.main(["qa", slug, "--reopen", "--new-file"])

    assert rc == EXIT_USAGE


def test_main_first_run_writes_base_silently(tmp_path, monkeypatch):
    slug = "e2e_first"
    plan_dir = _make_plan_dir(tmp_path, slug)  # recon only; no qa.md yet
    monkeypatch.setattr(qa_main, "_PLANS_DIR", tmp_path)
    _patch_sdk(monkeypatch, "FIRST")

    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        rc = qa_main.main(["qa", slug])

    assert rc == EXIT_OK
    assert (plan_dir / f"{slug}_qa.md").read_text(
        encoding="utf-8"
    ).rstrip("\n") == "FIRST"
    # Clean first-run write is silent on the re-run notice line.
    for token in ("Appended", "Reopened", "New file"):
        assert token not in err.getvalue()


# --------------------------------------------------------------------------- #
# Exit-code constant pins (cross-CLI parity — STILL VALID)                      #
# --------------------------------------------------------------------------- #


def test_exit_target_exists_no_reopen_numeric_value():
    """Pin EXIT_TARGET_EXISTS_NO_REOPEN at 8 and equal to the planner's.

    qa no longer RAISES this code, but it is retained in the closed enum so
    a chain-driver examining ``$?`` interprets the planner's overwrite-
    refusal uniformly across both CLIs. Pin the value against renumbering.
    """
    from bin._planner import exit_codes as planner_exit_codes
    from bin._qa import exit_codes as qa_exit_codes

    assert qa_exit_codes.EXIT_TARGET_EXISTS_NO_REOPEN == 8
    assert (
        qa_exit_codes.EXIT_TARGET_EXISTS_NO_REOPEN
        == planner_exit_codes.EXIT_TARGET_EXISTS_NO_REOPEN
    )


def test_exit_target_exists_no_reopen_in_all_codes():
    from bin._qa.exit_codes import ALL_CODES, EXIT_TARGET_EXISTS_NO_REOPEN

    assert EXIT_TARGET_EXISTS_NO_REOPEN in ALL_CODES
