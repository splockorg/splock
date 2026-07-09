"""G.5 — `auto_register.py` graceful no-op when §P substrate unavailable.

Per implplan §A.impl.5b + §P: the auto-register try/except wrapper
means chain-spawn doesn't halt when §P intent registry is absent.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def test_auto_register_exposes_callable_entry():
    """G.5a: auto_register module has a top-level callable for chain-spawn."""
    from bin._chain_overnight import auto_register

    # Per §A.impl.5b: the public entry should be `auto_register_chain_session`.
    assert hasattr(auto_register, "auto_register_chain_session"), (
        "auto_register module missing auto_register_chain_session entry"
    )
    assert callable(auto_register.auto_register_chain_session)


def test_auto_register_uses_try_except_wrapper(repo_root):
    """G.5b: source has try/except wrapper for graceful §P-unavailable degradation."""
    src = (repo_root / "bin" / "_chain_overnight" / "auto_register.py").read_text(
        encoding="utf-8"
    )
    # Look for try/except + ImportError or similar §P-unavailable handling.
    assert "try:" in src, "auto_register.py has no try block"
    has_graceful = (
        "ImportError" in src or
        "except Exception" in src or
        "_SETTINGS_KNOB_KEY" in src or  # gated via settings knob
        "graceful" in src.lower() or
        "no-op" in src.lower()
    )
    assert has_graceful, (
        "auto_register.py doesn't appear to have graceful-degradation handling "
        "for §P-unavailable case; spec requires try/except wrapper or settings gate"
    )
