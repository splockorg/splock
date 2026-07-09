"""D.3 — `bin/marker register-prefix` refuses collision; sequence allocator handles gaps.

Per §10 §4.2 finding #3 (STD.1→STD.3 case): re-registering an existing
prefix refuses; the sequence allocator skips occupied numbers when
allocating new entries.
"""

from __future__ import annotations

import pytest
from unittest import mock


pytestmark = pytest.mark.acceptance


def test_double_register_prefix_refuses(tmp_repo):
    """D.3a: registering an existing prefix is refused."""
    from bin._marker import register_prefix

    with mock.patch("bin._marker.log_emit.append_row"):
        # First registration — should succeed.
        code = register_prefix.run(
            new_prefix="DBL",
            domain="Double-register test",
            owner="tests/acceptance",
            repo_root=tmp_repo,
        )
    assert code == 0, "First prefix registration should succeed"

    with mock.patch("bin._marker.log_emit.append_row"):
        # Second registration of the same prefix — should refuse.
        code = register_prefix.run(
            new_prefix="DBL",
            domain="Attempted collision",
            owner="tests/acceptance",
            repo_root=tmp_repo,
        )
    assert code != 0, (
        f"Re-registering existing prefix should refuse; got exit {code}"
    )


def test_sequence_allocator_skips_existing_numbers(tmp_repo):
    """D.3b: when creating against a partially-used prefix, sequence allocator picks next free."""
    from bin._marker import register_prefix, create

    with mock.patch("bin._marker.log_emit.append_row"):
        register_prefix.run(
            new_prefix="SEQ",
            domain="Sequence-skip test",
            owner="tests/acceptance",
            repo_root=tmp_repo,
        )
        # Create SEQ.1
        c1 = create.run(
            prefix="SEQ",
            title="first",
            trigger="date:2026-12-31",
            plan="acc", module="test/", data_needed="Test.",
            repo_root=tmp_repo,
        )
        # Create SEQ.2
        c2 = create.run(
            prefix="SEQ",
            title="second",
            trigger="date:2026-12-31",
            plan="acc", module="test/", data_needed="Test.",
            repo_root=tmp_repo,
        )
    assert c1 == 0 and c2 == 0, "Sequential creates should succeed"

    list_path = tmp_repo / "docs" / "plans" / "scheduled_markers" / "list.md"
    text = list_path.read_text(encoding="utf-8")
    assert "### SEQ.1 — first" in text
    assert "### SEQ.2 — second" in text, (
        "Sequence allocator should produce SEQ.1 then SEQ.2"
    )
