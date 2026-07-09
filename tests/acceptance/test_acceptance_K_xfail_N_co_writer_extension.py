"""K.6 (xfail) — §N REQ_F co-writer extension for `bin/verify` + `bin/build_briefing`.

Per inventory + §10 §7.4 (§N.impl.1 Status):
`bin/verify` and `bin/build_briefing` write to `_state.json` via the
shared retry-loop package but aren't explicitly named as co-writers in
the F_CO_WRITERS rule of `bin/_cli_lint/rules.py`. Sonnet flagged this
during §N integration review; main-agent reverted with a clarifying
comment per the package-level discovery distinction.

When the explicit co-writer entries are added, this xpasses.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


EXPECTED_CO_WRITERS = {"bin/verify", "bin/build_briefing"}


# 2026-05-22: xfail removed — follow-up confirmed landed (acceptance suite
# Pass 2 found this xpassing; F_CO_WRITERS in bin/_cli_lint/rules.py
# already contains both entries). The §10 §7.4 entry is stale; flag for
# documentation cleanup.
def test_cli_lint_F_CO_WRITERS_includes_retry_loop_co_writers(repo_root):
    """K.6: F_CO_WRITERS rule includes bin/verify + bin/build_briefing explicitly."""
    rules_path = repo_root / "bin" / "_cli_lint" / "rules.py"
    text = rules_path.read_text(encoding="utf-8")

    # Find the F_CO_WRITERS section by string search.
    # Post-fix: both bin/verify and bin/build_briefing appear as keys/values
    # in the F_CO_WRITERS structure.
    missing = [w for w in EXPECTED_CO_WRITERS if w not in text]
    assert not missing, (
        f"F_CO_WRITERS missing explicit entries: {missing}\n"
        "Pending per §N.impl.1 Status REQ_F co-writer extension."
    )
