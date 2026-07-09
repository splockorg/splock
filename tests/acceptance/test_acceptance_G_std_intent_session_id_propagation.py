"""G.4 — `SPLOCK_INTENT_SESSION_ID` env var registered + propagated.

Per Opus M-9 + §10 §1.5 cross-section follow-up #4: chain-spawn auto-
register injects SPLOCK_INTENT_SESSION_ID into child process env post-
register. The env var is registered in §I.impl.3 under the
driver-set-chain-context class.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def test_std_intent_session_id_in_env_registry(repo_root):
    """G.4a: env_inventory registry contains SPLOCK_INTENT_SESSION_ID row."""
    try:
        from bin._env_inventory.registry import REGISTRY
    except ImportError as exc:
        pytest.fail(f"env_inventory.registry not importable: {exc}")

    # REGISTRY is dict[str, EnvVarSpec] keyed by env var name.
    names = set(REGISTRY.keys()) if isinstance(REGISTRY, dict) else \
            {getattr(s, "name", None) for s in REGISTRY}
    assert "SPLOCK_INTENT_SESSION_ID" in names, (
        "SPLOCK_INTENT_SESSION_ID not in env-inventory REGISTRY — "
        "cross-section follow-up #4 not applied"
    )


def test_auto_register_module_propagates_intent_session_id(repo_root):
    """G.4b: auto_register.py references SPLOCK_INTENT_SESSION_ID env propagation."""
    auto_register_src = (repo_root / "bin" / "_chain_overnight"
                         / "auto_register.py").read_text(encoding="utf-8")
    assert "SPLOCK_INTENT_SESSION_ID" in auto_register_src, (
        "auto_register.py doesn't propagate SPLOCK_INTENT_SESSION_ID"
    )
