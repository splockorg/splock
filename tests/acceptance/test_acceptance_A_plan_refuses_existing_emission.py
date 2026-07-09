"""A.2 — `bin/plan` refuses when `<slug>_plan.json` already exists."""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


def test_plan_refuses_when_emission_target_exists(tmp_repo, monkeypatch):
    """A.2: planner refuses (EXIT_USAGE) when the JSON emission target exists."""
    slug = "_acceptance_a2"
    plan_dir = tmp_repo / "docs" / "plans" / slug
    plan_dir.mkdir(parents=True)

    # Both recon (predecessor present) AND _plan.json (emission target already there).
    (plan_dir / f"{slug}_recon.md").write_text("# Recon\n", encoding="utf-8")
    (plan_dir / f"{slug}_plan.json").write_text(
        json.dumps({"schema_version": 1, "slug": slug}), encoding="utf-8"
    )

    monkeypatch.chdir(tmp_repo)

    from bin._planner import main as planner_main, exit_codes
    code = planner_main.main(["plan", slug])
    assert code == exit_codes.EXIT_USAGE, (
        f"Expected EXIT_USAGE when {slug}_plan.json exists; got {code}"
    )
