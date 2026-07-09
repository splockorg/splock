"""D.10 — `done → wip` refuses without `OPERATOR_OVERRIDE_STATE=1`; permits with override.

Per userguide §14.1 + §18 "Roll back a `done` task to `wip`".
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def _is_allowed(verdict) -> bool:
    """Tolerant accessor for TransitionVerdict result shape."""
    if hasattr(verdict, "allowed"):
        return bool(verdict.allowed)
    if hasattr(verdict, "valid"):
        return bool(verdict.valid)
    if hasattr(verdict, "outcome"):
        return verdict.outcome in ("allowed", "ok", "permitted")
    if hasattr(verdict, "verdict"):
        return verdict.verdict in ("allowed", "ok", "permitted")
    if isinstance(verdict, bool):
        return verdict
    return False


def test_done_to_wip_refuses_without_operator_override(monkeypatch):
    """D.10a: done→wip with override_active=False refuses."""
    from bin._update_orchestrator import canonical_transitions as ct

    monkeypatch.delenv("OPERATOR_OVERRIDE_STATE", raising=False)
    result = ct.validate_transition(
        from_status="done", to_status="wip", override_active=False,
    )
    assert not _is_allowed(result), (
        f"done→wip without override should refuse; verdict={result!r}"
    )


def test_done_to_wip_permits_with_operator_override():
    """D.10b: done→wip with override_active=True permits."""
    from bin._update_orchestrator import canonical_transitions as ct

    result = ct.validate_transition(
        from_status="done", to_status="wip", override_active=True,
    )
    assert _is_allowed(result), (
        f"done→wip with override=True should be permitted; verdict={result!r}"
    )
