"""D.1 — `bin/marker` create + show + close happy path round-trips through `list.md`.

Per inventory:
- Source: userguide §9.1.
- Predecessor: tmp_repo with empty `scheduled_markers/{list.md, prefix_registry.md}`.
- Expected outcome: create writes a marker row; show retrieves it; close
  moves it to the Closed section.
"""

from __future__ import annotations

import pytest
from unittest import mock


pytestmark = pytest.mark.acceptance


@pytest.fixture
def _registered_prefix(tmp_repo):
    """Pre-register a 3-letter prefix so create can allocate sequence."""
    from bin._marker import register_prefix
    with mock.patch("bin._marker.log_emit.append_row"):
        code = register_prefix.run(
            new_prefix="ACT",  # Acceptance-Test prefix
            domain="Acceptance test fixture",
            owner="tests/acceptance",
            repo_root=tmp_repo,
        )
    assert code == 0, "Prefix registration failed in fixture setup"
    return "ACT"


def test_marker_create_show_close_round_trip(tmp_repo, _registered_prefix):
    """D.1: full create → show → close cycle round-trips through list.md."""
    from bin._marker import close, create, show

    prefix = _registered_prefix

    # ----- Create --------------------------------------------------------
    with mock.patch("bin._marker.log_emit.append_row"):
        code = create.run(
            prefix=prefix,
            title="Acceptance harness mint",
            trigger="date:2026-12-31",
            plan="splock",
            module="tests/acceptance/",
            data_needed="Harness verification.",
            context="D.1 prove-the-harness test.",
            repo_root=tmp_repo,
        )
    assert code == 0, "marker create returned non-zero"

    list_path = tmp_repo / "docs" / "plans" / "scheduled_markers" / "list.md"
    text_after_create = list_path.read_text(encoding="utf-8")
    assert f"### {prefix}.1 — Acceptance harness mint" in text_after_create, (
        "Created marker not surfaced in Active section"
    )

    # ----- Show ---------------------------------------------------------
    with mock.patch("bin._marker.log_emit.append_row"):
        code = show.run(marker_id=f"{prefix}.1", repo_root=tmp_repo)
    assert code == 0, "marker show returned non-zero for just-created marker"

    # ----- Close --------------------------------------------------------
    with mock.patch("bin._marker.log_emit.append_row"):
        code = close.run(
            marker_id=f"{prefix}.1",
            resolution="acceptance harness complete",
            repo_root=tmp_repo,
        )
    assert code == 0, "marker close returned non-zero"

    text_after_close = list_path.read_text(encoding="utf-8")
    # After close, the marker should be in Closed section, not Active.
    active_section, _, closed_section = text_after_close.partition("## Closed entries")
    assert f"### {prefix}.1" not in active_section, (
        "Closed marker still in Active section"
    )
    assert f"### {prefix}.1" in closed_section, (
        "Closed marker not surfaced in Closed section"
    )
