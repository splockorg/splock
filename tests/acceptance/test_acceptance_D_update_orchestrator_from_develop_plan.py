"""D.11 — `--from-develop-plan` 6→7 status mapping applied correctly.

Per implplan §E.impl.3: develop-plan native 6 statuses map to the
canonical 7-status enum via deterministic LUT.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


# 6 develop-plan native statuses per implplan §E.impl.3 LUT.
DEVELOP_PLAN_NATIVE = {
    "not_started", "in_progress", "awaiting_eval",
    "revisions_requested", "completed", "blocked",
}


def test_develop_plan_lut_covers_all_six_native_statuses():
    """D.11: DEVELOP_PLAN_STATUS_LUT maps all 6 develop-plan native statuses."""
    from bin._update_orchestrator.from_develop_plan import DEVELOP_PLAN_STATUS_LUT

    lut_keys = set(DEVELOP_PLAN_STATUS_LUT.keys())
    missing = DEVELOP_PLAN_NATIVE - lut_keys
    assert not missing, (
        f"DEVELOP_PLAN_STATUS_LUT missing native statuses: {sorted(missing)}"
    )


def test_develop_plan_lut_maps_to_canonical_7_statuses():
    """D.11b: LUT values map ONLY to the canonical 7-status enum."""
    from bin._update_orchestrator.from_develop_plan import DEVELOP_PLAN_STATUS_LUT

    canonical_7 = {"ready", "wip", "done", "deferred", "blocked", "cancelled", "unknown"}
    drift = []
    for native, mapping in DEVELOP_PLAN_STATUS_LUT.items():
        target = mapping.get("canonical") if isinstance(mapping, dict) else None
        if target is None:
            drift.append((native, "no `canonical` key"))
        elif target not in canonical_7:
            drift.append((native, f"maps to {target!r} (outside 7-status enum)"))
    assert not drift, (
        f"DEVELOP_PLAN_STATUS_LUT entries drift from canonical 7-status enum:\n"
        + "\n".join(f"  {n}: {issue}" for n, issue in drift)
    )
