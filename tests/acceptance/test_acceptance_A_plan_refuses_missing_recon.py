"""A.1 — `bin/plan widget` refuses (exit 1) when `widget_recon.md` does not exist.

Per inventory:
- Source: userguide §2.1 Step 4; quickstart five-step workflow gate.
- Predecessor: tmp_slug_dir exists (plan dir present) but no _recon.md.
- Expected outcome: planner CLI returns EXIT_USAGE (1) with structured
  stderr indicating the missing predecessor.
"""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


def test_plan_refuses_missing_recon(tmp_repo, monkeypatch, capsys):
    """A.1: planner CLI refuses when <slug>_recon.md is absent."""
    slug = "_acceptance_a1_slug"
    (tmp_repo / "docs" / "plans" / slug).mkdir(parents=True)
    # Deliberately do NOT create _recon.md.

    monkeypatch.chdir(tmp_repo)

    # Import inside the test so PYTHONPATH resolution against the real repo
    # works regardless of cwd (the module lives in the repo root's bin/).
    from bin._planner import main as planner_main
    from bin._planner import exit_codes

    code = planner_main.main(["plan", slug])

    assert code == exit_codes.EXIT_USAGE, (
        f"Expected EXIT_USAGE ({exit_codes.EXIT_USAGE}) when {slug}_recon.md "
        f"missing; got {code}."
    )

    # Stderr should be structured JSON per cross-cutting CLI discipline.
    captured = capsys.readouterr()
    # The CLI emits JSON to stderr; we don't enforce exact shape here
    # (Block J.7 will), just that something was written.
    assert captured.err.strip(), "Expected structured error on stderr"
    # Best-effort: ensure the error mentions recon (the missing predecessor).
    err_lower = captured.err.lower()
    assert "recon" in err_lower or "usage" in err_lower, (
        f"Expected error to reference recon/usage; got: {captured.err!r}"
    )
