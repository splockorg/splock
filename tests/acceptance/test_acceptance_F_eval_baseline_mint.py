"""F.1 — `bin/eval-baseline mint --slug X` creates _baseline/<version>/ snapshot."""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def test_eval_baseline_module_main_exists():
    """F.1a: bin._eval_baseline.main has a callable main entry."""
    from bin._eval_baseline import main as baseline_main
    assert callable(baseline_main.main), "main() not exposed"


def test_eval_baseline_module_parser_accepts_mint_invocation():
    """F.1b: parser accepts the actual CLI shape: positional slug + --mint NAME.

    Userguide §11 was updated 2026-05-22 (post-Pass 5 Finding 7) to match
    the actual shape (`bin/eval-baseline <slug> --mint <name>`); prior to
    that the doc showed a non-existent `mint` subcommand form.
    """
    from bin._eval_baseline.main import _build_parser

    parser = _build_parser()
    try:
        args = parser.parse_args(["_acceptance_f1", "--mint", "baseline_v1"])
    except SystemExit as exc:
        pytest.fail(
            f"positional-slug + --mint should parse; got SystemExit({exc.code})"
        )
    assert hasattr(args, "slug"), "Parsed args missing slug"
    assert args.slug == "_acceptance_f1"
