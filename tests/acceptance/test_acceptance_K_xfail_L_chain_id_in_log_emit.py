"""K.3 (xfail) — §L F-04 `chain_id` omission in `_route_issue/log_emit.py`.

Per inventory + §10 §7.4 (§L.impl.1 Status):
`bin/_route_issue/log_emit.py` does not include `chain_id` in emitted rows.
§L is operator-facing with low blast-radius, so deferred.

When chain_id is added, this xpasses + owner removes decorator.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


@pytest.mark.xfail(
    reason="§L F-04 chain_id omission pending per §L.impl.1 Status",
    strict=False,
)
def test_route_issue_log_emit_includes_chain_id(repo_root):
    """K.3: bin/_route_issue/log_emit.py includes chain_id in log row payload."""
    log_emit_path = repo_root / "bin" / "_route_issue" / "log_emit.py"
    if not log_emit_path.exists():
        pytest.fail(f"log_emit.py missing: {log_emit_path}")

    text = log_emit_path.read_text(encoding="utf-8")
    # Post-fix: chain_id should appear as a row field.
    assert "chain_id" in text, (
        "_route_issue/log_emit.py does not reference chain_id — "
        "F-04 fix pending"
    )
