"""D.2 — `bin/marker create` refuses open-ended trigger.

Per userguide §9.3: markers MUST have a concrete closure trigger
(edit: / date: / condition: prefix). Open-ended triggers ("eventually",
"someday") are refused.
"""

from __future__ import annotations

import pytest
from unittest import mock


pytestmark = pytest.mark.acceptance


@pytest.fixture
def _registered_prefix(tmp_repo):
    from bin._marker import register_prefix
    with mock.patch("bin._marker.log_emit.append_row"):
        register_prefix.run(
            new_prefix="OPN",
            domain="Open-trigger refusal test",
            owner="tests/acceptance",
            repo_root=tmp_repo,
        )
    return "OPN"


def test_marker_create_refuses_eventually_trigger(tmp_repo, _registered_prefix):
    """D.2a: `--trigger 'eventually'` (no closed prefix) is refused."""
    from bin._marker import create

    with mock.patch("bin._marker.log_emit.append_row"):
        code = create.run(
            prefix=_registered_prefix,
            title="should be refused",
            trigger="eventually",  # No edit:/date:/condition: prefix
            plan="splock",
            module="test/",
            data_needed="Test.",
            repo_root=tmp_repo,
        )
    assert code != 0, "Open-ended trigger should be refused; got exit 0"


def test_marker_create_accepts_date_trigger(tmp_repo, _registered_prefix):
    """D.2b: concrete `date:YYYY-MM-DD` trigger is accepted."""
    from bin._marker import create

    with mock.patch("bin._marker.log_emit.append_row"):
        code = create.run(
            prefix=_registered_prefix,
            title="legitimate marker",
            trigger="date:2026-12-31",
            plan="splock",
            module="test/",
            data_needed="Test.",
            repo_root=tmp_repo,
        )
    assert code == 0, f"Concrete date trigger should succeed; got exit {code}"
