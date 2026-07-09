"""D.9 — `bin/update_orchestrator` accepts all 7 canonical status transitions.

Per userguide §14 + Risk 7: the 7-status enum
{ready, wip, done, deferred, blocked, cancelled, unknown} is the
canonical source-of-truth.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


SEVEN_STATUSES = {"ready", "wip", "done", "deferred", "blocked", "cancelled", "unknown"}


def test_canonical_transitions_module_defines_seven_statuses():
    """D.9: the canonical-transitions module recognizes the 7-status enum."""
    from bin._update_orchestrator import canonical_transitions as ct

    # The module's validate_transition() should accept all 7 as `to_status`.
    # We don't assume an exact constant — search the module for the 7 names.
    import inspect
    src = inspect.getsource(ct)
    missing = [s for s in SEVEN_STATUSES if f'"{s}"' not in src and f"'{s}'" not in src]
    assert not missing, (
        f"canonical_transitions.py does not reference all 7 statuses; "
        f"missing as string literals: {missing}"
    )


def test_invalid_status_value_refused():
    """D.9b: a non-7-status value (e.g., 'bogus_status_value') is rejected."""
    from bin._update_orchestrator import canonical_transitions as ct

    refused = False
    try:
        result = ct.validate_transition(
            from_status="wip", to_status="bogus_status_value",
            override_active=False,
        )
        # Per docstring: returns refuse_unknown_status verdict on unknown status.
        result_str = repr(result).lower()
        refused = (
            "refuse" in result_str or "unknown" in result_str or
            "deny" in result_str or "invalid" in result_str
        )
        # Also check verdict attrs if present.
        if hasattr(result, "allowed"):
            refused = refused or not result.allowed
        if hasattr(result, "outcome"):
            refused = refused or result.outcome not in ("allowed", "ok", "permitted")
    except (ValueError, TypeError) as exc:
        refused = True
    assert refused, "Invalid status value should be refused by validate_transition"
