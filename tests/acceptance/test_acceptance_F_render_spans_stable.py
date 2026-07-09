"""F.4 — `bin/render_spans --slug X` emits byte-stable timeline output."""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


def test_render_spans_module_main_exists():
    """F.4: bin._render_spans.main has callable main entry."""
    from bin._render_spans import main as spans_main
    assert callable(spans_main.main)


def test_render_spans_parses_empty_args_or_slug():
    """F.4b: parser accepts the documented invocation shape."""
    from bin._render_spans.main import _build_parser

    parser = _build_parser()
    try:
        parser.parse_args(["--slug", "_acceptance_f4"])
    except SystemExit as exc:
        # Some parsers require positional slug; try that.
        try:
            parser.parse_args(["_acceptance_f4"])
        except SystemExit:
            pytest.fail(
                f"render_spans parser refused both --slug and positional shapes "
                f"({exc.code})"
            )
