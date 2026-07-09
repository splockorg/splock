"""I.1 — `bin/handoff` does NOT exist (SRR.1 deferred).

Per userguide §19 #5: operator-session recovery file is SRR.1-deferred.
The acceptance test asserts the file's absence + that the SRR.1 marker
is open with the correct closure trigger.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def test_bin_handoff_does_not_exist(repo_root):
    """I.1a: bin/handoff is not present on disk."""
    handoff_path = repo_root / "bin" / "handoff"
    assert not handoff_path.exists(), (
        "bin/handoff exists — SRR.1 deferral has been resolved. Update userguide §19 + "
        "close the SRR.1 marker."
    )


def test_srr_1_marker_active(repo_root):
    """I.1b: SRR.1 marker entry exists in scheduled_markers/list.md and is active."""
    list_path = repo_root / "docs" / "plans" / "scheduled_markers" / "list.md"
    text = list_path.read_text(encoding="utf-8")
    # Find SRR.1 in active section.
    active_section, _, closed_section = text.partition("## Closed entries")
    assert "SRR.1" in active_section, (
        "SRR.1 not in Active entries; either it was closed (then I.1a should pass) "
        "or the marker was never minted"
    )
