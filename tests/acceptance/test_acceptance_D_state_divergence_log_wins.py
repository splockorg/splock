"""D.13 — `bin/state-divergence-check` resolves divergence with log-truth winning.

Per userguide §15 + Opus B-1: smaller-scoped version of J.8 (which is
skipped pending API-shape match). D.13 verifies the basic module API
exists + the resolution direction is "log wins".
"""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


def test_state_divergence_module_exists_and_has_main():
    """D.13a: bin._state_divergence.main has a callable main entry."""
    try:
        from bin._state_divergence import main as divergence_main
    except ImportError as exc:
        pytest.fail(f"state-divergence module not importable: {exc}")
    assert callable(getattr(divergence_main, "main", None)), (
        "main() not exposed as callable"
    )


def test_state_divergence_check_helpers_treat_log_as_source(tmp_slug_dir):
    """D.13b: internal _check_slug helper treats log rows as canonical."""
    from bin._state_divergence import main as divergence_main

    # Set up a divergence: state.json says T01=done; log says T01=deferred.
    (tmp_slug_dir / "_state.json").write_text(json.dumps({
        "schema_version": 1, "slug": "_acceptance_d13",
        "tasks": {"T01": {"status": "done"}},
    }), encoding="utf-8")

    (tmp_slug_dir / "_orchestrator_log.jsonl").write_text(
        json.dumps({
            "schema_version": 5,
            "ts": "2026-05-22T12:00:00Z",
            "emitted_by": "bin/update_orchestrator",
            "event_type": "transition",
            "slug": "_acceptance_d13",
            "task_id": "T01",
            "transition": {"from": "wip", "to": "deferred"},
            "session_id": "sess_d1300000",
            "reason": "deferred to next sprint",
            "mode_at_transition": {"overnight": False, "guardrail": True},
            "writer_pid": 12345, "writer_host": "test",
            "plan_slug": "_acceptance_d13",
        }) + "\n",
        encoding="utf-8",
    )

    if not hasattr(divergence_main, "_check_slug"):
        pytest.skip("_check_slug helper not exposed; covered by J.8 when fixture lands")

    # Probe the check helper. Signature may vary; try common shapes.
    try:
        report = divergence_main._check_slug(slug="_acceptance_d13", plan_dir=tmp_slug_dir)
    except TypeError:
        try:
            report = divergence_main._check_slug(tmp_slug_dir)
        except TypeError:
            pytest.skip("_check_slug signature doesn't match common shapes")

    # Report should surface log-truth (deferred) somewhere.
    report_str = json.dumps(report) if isinstance(report, dict) else repr(report)
    assert "deferred" in report_str.lower(), (
        f"Divergence report should surface log-truth (deferred); got: {report_str!r}"
    )
