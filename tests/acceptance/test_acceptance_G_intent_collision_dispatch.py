"""G.3 — `bin/intent` collision detected → morning-review entry with `collision_detected`.

Per implplan §P.impl.5 + §H.impl deferral_reason enum extension.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def test_intent_dispatch_module_exposes_collision_path(repo_root):
    """G.3: bin/_intent/dispatch.py references collision dispatch surface."""
    dispatch = repo_root / "bin" / "_intent" / "dispatch.py"
    if not dispatch.exists():
        pytest.skip("bin/_intent/dispatch.py missing")
    text = dispatch.read_text(encoding="utf-8")
    indicators = ["collision_detected", "collision", "morning-review",
                  "morning_review"]
    found = [s for s in indicators if s in text]
    assert found, (
        f"dispatch.py doesn't reference collision-dispatch concept: {indicators}"
    )


def test_morning_review_deferral_reason_includes_collision_detected(repo_root):
    """G.3b: morning-review entry_format enum includes `collision_detected`."""
    entry_format = repo_root / "bin" / "_morning_review" / "entry_format.py"
    if not entry_format.exists():
        pytest.skip("bin/_morning_review/entry_format.py missing")
    text = entry_format.read_text(encoding="utf-8")
    assert "collision_detected" in text, (
        "Pre-flight follow-up #2 (deferral_reason: collision_detected enum extension) "
        "not applied to morning-review entry_format"
    )
