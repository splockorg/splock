"""K.2 — Slug pattern documented in userguide.

**Originally** tracked §J F-06 event_type closed-enum bump per §J.impl.1
Status. Acceptance Pass 5 follow-up retraction (2026-05-22): the
orchestrator_log_v1 schema has no `event_type` enum at all — that's by
design (event_type is an additive payload field per §J.impl.4 / §M.impl.5).
What Pass 2 actually surfaced when J.9 failed was the **slug-pattern
constraint** (`^[a-z0-9][a-z0-9_-]*$` — slugs can't start with `_`).

This test now verifies the slug-pattern requirement is at least mentioned
somewhere in the user-facing docs so operators don't get cryptic
SchemaValidationError when using `_test_` style slugs.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


# 2026-05-22: xfail removed — slug-pattern documentation landed in userguide §2.
def test_slug_pattern_constraint_documented(repo_root):
    """K.2: userguide / quickstart mentions the slug naming pattern."""
    userguide = (repo_root / "docs" / "guides" / "splock_userguide.md").read_text(
        encoding="utf-8"
    )
    quickstart = (repo_root / "docs" / "guides" / "splock_quickstart.md").read_text(
        encoding="utf-8"
    )
    combined = userguide + "\n" + quickstart
    # The pattern lives in `schemas/orchestrator_log_v1.schema.json` $defs.Slug.
    # Userguide should mention "must start with a letter or digit" or equivalent.
    indicators = [
        "[a-z0-9][a-z0-9_-]",      # the regex itself
        "start with",               # natural-language hint
        "must not start with _",
        "slug naming",
        "slug pattern",
    ]
    found = [s for s in indicators if s in combined.lower() or s in combined]
    assert found, (
        "Slug-pattern constraint not documented anywhere in user-facing guides. "
        "Operators using `_test_*` style slugs get cryptic JSONL "
        "SchemaValidationError on first write."
    )
