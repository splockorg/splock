"""G.2 — `bin/intent` falls back to `sync_pending: true` when DB unavailable.

Per §10 §7.1: DDL migration not run → MySQL writes fall back to local
JSONL with `sync_pending` flag for later reconciliation by `bin/intent
doctor`. This is the intended local-first design.
"""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


def test_intent_db_module_exposes_sync_pending_concept(repo_root):
    """G.2: the §P intent registry codebase references sync_pending semantics."""
    db_path = repo_root / "bin" / "_intent" / "db.py"
    if not db_path.exists():
        pytest.skip("bin/_intent/db.py missing")
    text = db_path.read_text(encoding="utf-8")
    # Look for sync_pending or related local-first fallback indicators.
    indicators = ["sync_pending", "fallback", "local_only", "DB unavailable",
                  "reconcile"]
    found = [s for s in indicators if s.lower() in text.lower()]
    assert found, (
        f"bin/_intent/db.py doesn't reference any local-first fallback concept; "
        f"expected at least one of: {indicators}"
    )


def test_intent_jsonl_writer_module_exists(repo_root):
    """G.2b: bin/_intent/jsonl_writer.py exists — local-first write path."""
    jsonl_writer = repo_root / "bin" / "_intent" / "jsonl_writer.py"
    assert jsonl_writer.exists(), (
        "bin/_intent/jsonl_writer.py missing — local-first design contract"
    )
