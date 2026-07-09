"""qa CLI argparse — `--directive` flag shape.

Per std_command_operator_extensions task TE (covers SC10 qa half +
argparse-shape contract). Verifies the argparse layer accepts the new
flag in the three documented shapes:

1. `bin/qa qa <slug>` (no directive) — `args.directive is None`.
2. `bin/qa qa <slug> --directive 'x'` — `args.directive == 'x'`.
3. `bin/qa qa <slug> --directive '<8KB+1 bytes>'` — argparse parses
   successfully, but `_build_inputs` (driven via `main(...)`) refuses
   with exit code 1 (the cap is in the post-argparse validation layer,
   not in a `type=` callable, so that the exit-code envelope from
   `bin._qa.main.main` is uniform).

These tests exercise the CLI argument-parser surface so future
refactors of `_build_parser` keep the new flag's shape stable
(including: optional, single-arg, default None).
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout


from bin._qa import exit_codes
from bin._qa.main import _build_parser, main


# ----------------------------------------------------------------------
# Argparse shape
# ----------------------------------------------------------------------

def test_argparse_accepts_directive_with_value() -> None:
    """`bin/qa qa <slug> --directive 'focus on gate logic'` parses; the
    resulting namespace has the directive verbatim."""
    parser = _build_parser()
    args = parser.parse_args(
        ["qa", "example_slug", "--directive", "focus on gate logic"]
    )
    assert args.directive == "focus on gate logic"
    assert args.slug == "example_slug"


def test_argparse_directive_defaults_to_none_when_absent() -> None:
    """`bin/qa qa <slug>` (no --directive) parses; `args.directive is None`."""
    parser = _build_parser()
    args = parser.parse_args(["qa", "example_slug"])
    assert args.directive is None


def test_argparse_accepts_empty_string_directive() -> None:
    """`--directive ''` is parseable (an empty string is distinct from
    omission). The size-cap layer accepts empty strings (0 bytes ≤ 8192)."""
    parser = _build_parser()
    args = parser.parse_args(["qa", "example_slug", "--directive", ""])
    assert args.directive == ""


def test_argparse_accepts_directive_with_special_characters() -> None:
    """The flag accepts shell-special characters (quoting is the caller's
    responsibility; argparse just reads `sys.argv`)."""
    parser = _build_parser()
    payload = "redo this: --reopen=true & focus on <ops>"
    args = parser.parse_args(
        ["qa", "example_slug", "--directive", payload]
    )
    assert args.directive == payload


def test_argparse_directive_long_string_parses(tmp_path) -> None:
    """The argparse layer does NOT enforce the size cap (that lives in
    `_build_inputs`). An 8193-byte directive parses successfully here."""
    parser = _build_parser()
    payload = "x" * 8193
    args = parser.parse_args(
        ["qa", "example_slug", "--directive", payload]
    )
    assert args.directive == payload
    assert len(args.directive.encode("utf-8")) == 8193


# ----------------------------------------------------------------------
# End-to-end main(...) — size cap maps to exit code 1 (EXIT_USAGE)
# ----------------------------------------------------------------------

def _make_plan_dir(tmp_path, slug: str = "example_slug"):
    """Create a minimal plan dir with the recon artifact present and
    no pre-existing qa.md."""
    plan_dir = tmp_path / slug
    plan_dir.mkdir()
    (plan_dir / f"{slug}_recon.md").write_text(
        "# recon\nsome body\n", encoding="utf-8"
    )
    return plan_dir


def test_main_refuses_directive_over_cap_with_exit_usage(tmp_path, monkeypatch) -> None:
    """`main(['qa', slug, '--directive', <8193 bytes>])` returns exit
    code 1 (EXIT_USAGE) and emits a structured-error JSON envelope on
    stderr naming `directive exceeds 8KB limit`."""
    plan_dir = _make_plan_dir(tmp_path)

    # Redirect _PLANS_DIR so the resolver looks under tmp_path
    monkeypatch.setattr("bin._qa.main._PLANS_DIR", tmp_path)

    payload = "x" * 8193
    err_buf = io.StringIO()
    out_buf = io.StringIO()
    with redirect_stderr(err_buf), redirect_stdout(out_buf):
        rc = main(["qa", "example_slug", "--directive", payload])

    assert rc == exit_codes.EXIT_USAGE, (
        f"expected EXIT_USAGE ({exit_codes.EXIT_USAGE}); got {rc}"
    )
    stderr_text = err_buf.getvalue()
    assert "directive exceeds 8KB limit" in stderr_text, (
        f"expected SC10 stderr message; got: {stderr_text!r}"
    )
    assert "8193 bytes" in stderr_text


def test_main_accepts_directive_at_8192_bytes(tmp_path, monkeypatch) -> None:
    """`main(['qa', slug, '--directive', <8192 bytes>])` passes the size
    cap. We stub out `invoke_qa` so the test doesn't hit the SDK; the
    test only proves that the size cap permits exactly 8192 bytes (the
    SDK-call path is exercised in other tests)."""
    plan_dir = _make_plan_dir(tmp_path)
    monkeypatch.setattr("bin._qa.main._PLANS_DIR", tmp_path)

    # Stub invoke_qa so the test doesn't hit the SDK
    captured = {}

    def _stub_invoke_qa(*, slug, inputs, chain_id, **_kw):
        captured["slug"] = slug
        captured["inputs"] = inputs
        captured["chain_id"] = chain_id
        from bin._qa.invoke import QaResult
        return QaResult(
            qa_md="# qa\nstub body\n",
            cost_usd=0.0,
            model_id="stub-model",
            attempt_count=1,
        )

    monkeypatch.setattr("bin._qa.main.invoke_qa", _stub_invoke_qa)

    payload = "x" * 8192
    err_buf = io.StringIO()
    out_buf = io.StringIO()
    with redirect_stderr(err_buf), redirect_stdout(out_buf):
        rc = main(["qa", "example_slug", "--directive", payload, "--stdout"])

    assert rc == exit_codes.EXIT_OK, (
        f"8192-byte directive should pass size cap; got rc={rc}, "
        f"stderr={err_buf.getvalue()!r}"
    )
    # Confirm the stubbed invoke_qa actually saw the directive in QaInputs
    assert captured["inputs"].directive == payload


def test_main_with_no_directive_passes_none_to_inputs(tmp_path, monkeypatch) -> None:
    """`main(['qa', slug])` (no --directive) constructs QaInputs with
    `directive=None`. Pins the omission-when-absent behaviour."""
    plan_dir = _make_plan_dir(tmp_path)
    monkeypatch.setattr("bin._qa.main._PLANS_DIR", tmp_path)

    captured = {}

    def _stub_invoke_qa(*, slug, inputs, chain_id, **_kw):
        captured["inputs"] = inputs
        from bin._qa.invoke import QaResult
        return QaResult(
            qa_md="# qa\nstub body\n",
            cost_usd=0.0,
            model_id="stub-model",
            attempt_count=1,
        )

    monkeypatch.setattr("bin._qa.main.invoke_qa", _stub_invoke_qa)

    err_buf = io.StringIO()
    out_buf = io.StringIO()
    with redirect_stderr(err_buf), redirect_stdout(out_buf):
        rc = main(["qa", "example_slug", "--stdout"])

    assert rc == exit_codes.EXIT_OK, (
        f"no-directive invocation should succeed; got rc={rc}, "
        f"stderr={err_buf.getvalue()!r}"
    )
    assert captured["inputs"].directive is None
