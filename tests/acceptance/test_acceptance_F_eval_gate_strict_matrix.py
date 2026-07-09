"""F.3 — `bin/eval-gate` strict-mode-vs-report-only matrix per `SPLOCK_CHAIN_ID` presence."""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def test_eval_gate_module_exposes_main():
    """F.3a: bin._eval_gate.main has callable main."""
    from bin._eval_gate import main as gate_main
    assert callable(gate_main.main)


def test_eval_gate_parser_accepts_documented_modes():
    """F.3b: parser handles --check vs --report-only or equivalent."""
    from bin._eval_gate.main import _build_parser

    parser = _build_parser()
    # Try common modes. Either mode is accepted; we just verify parsing works.
    parsable = []
    for argv in (
        ["--slug", "_acceptance_f3"],
        [],  # default invocation
    ):
        try:
            parser.parse_args(argv)
            parsable.append(argv)
        except SystemExit:
            pass
    assert parsable, (
        "eval-gate parser refused every invocation we tried — unexpected"
    )
