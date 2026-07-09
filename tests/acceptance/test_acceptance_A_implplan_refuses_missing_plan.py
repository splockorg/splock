"""A.3 — `bin/implplan` refuses when `<slug>_plan.json` is missing."""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def test_implplan_refuses_missing_plan_json(tmp_repo, monkeypatch):
    """A.3: implplan refuses (EXIT_USAGE) when predecessor _plan.json missing."""
    slug = "_acceptance_a3"
    plan_dir = tmp_repo / "docs" / "plans" / slug
    plan_dir.mkdir(parents=True)

    # Recon present, but _plan.json absent (the predecessor implplan needs).
    (plan_dir / f"{slug}_recon.md").write_text("# Recon\n", encoding="utf-8")

    monkeypatch.chdir(tmp_repo)

    from bin._planner import main as planner_main, exit_codes
    code = planner_main.main(["implplan", slug])
    assert code == exit_codes.EXIT_USAGE, (
        f"Expected EXIT_USAGE when {slug}_plan.json missing; got {code}"
    )
