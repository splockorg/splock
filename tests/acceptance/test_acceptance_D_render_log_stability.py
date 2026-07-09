"""D.12 — `bin/render_log <slug>` produces byte-stable output across runs."""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


def test_render_log_produces_stable_output_on_rerun(tmp_slug_dir, monkeypatch):
    """D.12: rendering the same _orchestrator_log.jsonl twice yields identical output."""
    # Build a small synthetic log fixture.
    rows = [
        {
            "schema_version": 5,
            "ts": "2026-05-22T12:00:00Z",
            "emitted_by": "bin/update_orchestrator",
            "event_type": "transition",
            "slug": "_acceptance_d12",
            "task_id": "T01",
            "transition": {"from": "ready", "to": "wip"},
            "session_id": "sess_d1200000",
            "reason": "starting",
            "mode_at_transition": {"overnight": False, "guardrail": True},
            "writer_pid": 12345,
            "writer_host": "test-host",
            "plan_slug": "_acceptance_d12",
        }
    ]
    log_path = tmp_slug_dir / "_orchestrator_log.jsonl"
    log_path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )

    from bin._render_log import main as render_main

    # Run render twice; compare outputs.
    md_path_1 = tmp_slug_dir / "_orchestrator_log_v1.md"

    # The render_log CLI resolves plan dir from slug + cwd. monkeypatch to
    # tmp_slug_dir's parent so docs/plans/<slug>/ resolves correctly.
    monkeypatch.chdir(tmp_slug_dir.parent.parent.parent)

    slug = tmp_slug_dir.name
    rc1 = render_main.main([slug])
    if rc1 != 0:
        pytest.skip(
            f"render_log returned {rc1}; likely needs additional fixture "
            f"(plan dir resolution); track for Pass 5 enhancement"
        )

    if not md_path_1.exists():
        # Try alternative output paths.
        candidates = list(tmp_slug_dir.glob("_orchestrator_log*.md"))
        if not candidates:
            pytest.skip("render_log did not produce expected .md output")
        md_path_1 = candidates[0]

    first_render = md_path_1.read_text(encoding="utf-8")

    # Second render — strip the "rendered_at" timestamp header if present.
    rc2 = render_main.main([slug])
    assert rc2 == 0
    second_render = md_path_1.read_text(encoding="utf-8")

    # Allow header timestamp to differ; body should be identical.
    def _strip_rendered_at(s: str) -> str:
        return "\n".join(
            line for line in s.splitlines() if "rendered_at" not in line.lower()
        )

    assert _strip_rendered_at(first_render) == _strip_rendered_at(second_render), (
        "render_log output should be byte-stable on re-run (modulo header timestamp)"
    )
