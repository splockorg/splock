"""C.4 — Driver-level `pre_stage.py` refuses staging when path is credential-shaped.

Per inventory + orchestrator §4a.5 RATIFIED shape (a): the dual-altitude
defense requires (1) the agent-side `chain-sealed-state-delete-block` hook
PLUS (2) the driver-side `pre_stage.py` scan because PreToolUse hooks
don't fire on the driver's own shell `git add` invocations.

Also asserts the load-bearing source-comment header is present in
`pre_stage.py` per the residual obligation.
"""

from __future__ import annotations

import pytest
from pathlib import Path


pytestmark = pytest.mark.acceptance


def test_pre_stage_scans_for_credential_paths(repo_root, tmp_path):
    """C.4a: pre_stage.scan_blocklist refuses credential-shaped paths."""
    from bin._chain_overnight import pre_stage

    # Build a candidate staged-paths set including credential-shaped patterns.
    candidates = ("src/main.py", ".env", "src/.env.prod", "config.yml")
    blocklist = pre_stage.load_blocklist(repo_root)
    assert blocklist, "sealed_paths.txt produced empty blocklist"

    result = pre_stage.scan_blocklist(candidates, blocklist=blocklist)
    # ScanResult has `verdict` ("pass"/"refuse") + `matched_paths` + `matched_patterns`.
    assert result.verdict == "refuse", (
        f"pre_stage.scan_blocklist should have refused .env paths; got verdict={result.verdict}"
    )
    assert result.matched_paths, "Expected at least one matched path"
    assert any(".env" in p for p in result.matched_paths), (
        f"Expected .env-shaped path in matches; got {result.matched_paths}"
    )


def test_pre_stage_source_comment_names_platform_constraint(repo_root):
    """C.4b: pre_stage.py header comment names the PreToolUse-doesn't-fire constraint.

    Per orchestrator §4a.5 residual obligation: future architects must
    understand the duplication is *because of* a platform constraint, not
    *despite* one.
    """
    pre_stage_src = (repo_root / "bin" / "_chain_overnight" / "pre_stage.py").read_text(
        encoding="utf-8"
    )
    # The header should explicitly name PreToolUse + the driver-shell context.
    indicators = ["PreToolUse", "driver", "dual"]
    found = [s for s in indicators if s.lower() in pre_stage_src.lower()]
    assert len(found) >= 2, (
        f"pre_stage.py header comment doesn't sufficiently name the "
        f"PreToolUse + driver-shell + dual-altitude constraint; "
        f"found only {found}"
    )
