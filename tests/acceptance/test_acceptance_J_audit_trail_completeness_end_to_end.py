"""J.20 — Audit-trail completeness across load-bearing gates.

Per inventory + userguide §11 + §5.3 + plan §J anchor §4a.2:
operator-as-terminator forensic-trail contract — every load-bearing
gate must leave a row in SOME audit sink (`_orchestrator_log.jsonl` /
`_spans.jsonl` / `hook_log.jsonl` / morning-review file) so the
operator can reconstruct what happened post-hoc.

This test exercises a representative slice of gates end-to-end and
asserts each produces ≥1 row in its expected sink. If a gate slips
silent, the audit-trail-completeness claim breaks.

Gates exercised:
  (a) route_issue fix-now dispatch → orchestrator_log row
  (b) halt_handoff tampering_detected write → orchestrator_log row +
      morning-review file
  (c) completion_summary emit → summary file (LAST gesture per §A.5a)
  (d) sealed-paths deny → hook_log row (via bin/hook-log) — verified
      indirectly: the hook's invocation surface MUST call hook-log

Negative completion-summary-style gates (sealed-paths.sh, suppression-
block.sh) emit to `bin/hook-log` from within the shell wrapper. The
wrapper code path is verified by inspection rather than execution
because `bin/hook-log` writes under the user's home directory and we
don't want acceptance tests touching that surface.
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path


pytestmark = pytest.mark.acceptance


def _read_log_rows(plan_dir: Path) -> list[dict]:
    log = plan_dir / "_orchestrator_log.jsonl"
    if not log.exists():
        return []
    rows: list[dict] = []
    for line in log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


# ---------------------------------------------------------------------------
# (a) route_issue fix-now → orchestrator_log row
# ---------------------------------------------------------------------------

def test_audit_route_issue_fix_now_emits_row(tmp_slug_dir, monkeypatch):
    """J.20a: route_issue fix-now dispatch lands in _orchestrator_log.jsonl."""
    from bin._route_issue import fix_now, log_emit

    monkeypatch.setattr(
        log_emit, "resolve_plan_dir",
        lambda plan_dir, plan_slug: tmp_slug_dir,
    )
    rc = fix_now.run(
        description="audit-trail check (J.20a)",
        context="J20a:test",
        dry_run=False,
        json_output=False,
        repo_root=None,
        plan_slug=None,
    )
    assert rc == 0

    rows = _read_log_rows(tmp_slug_dir)
    assert rows, "route_issue fix-now produced no orchestrator_log row"
    assert any(
        r.get("event_type") == "fix_now_logged" or "fix_now" in r.get("reason", "")
        for r in rows
    ), f"no fix_now_logged event in emitted rows: {rows}"


# ---------------------------------------------------------------------------
# (b) halt_handoff tampering_detected → orchestrator_log + morning-review
# ---------------------------------------------------------------------------

def test_audit_halt_handoff_tampering_emits_in_both_sinks(tmp_slug_dir):
    """J.20b: halt_handoff tampering_detected emits row + morning-review section."""
    from bin._retry_loop import halt_handoff
    from bin._retry_loop.iteration_loop import IterationRecord

    record = IterationRecord(
        iteration_n=1,
        started_at="2026-05-22T12:00:00Z",
        ended_at="2026-05-22T12:01:00Z",
        test_runner_exit_code=1,
        failing_tests=["tests/foo/test_x.py::test_x"],
        diff_excerpt="@@ -1 +1 @@\n-pass\n+broken\n",
        rubric={
            "R1_root_cause": "test softened",
            "R2_what_missed": "regression case",
            "R3_next_action": "revert + fix",
            "R4_tampering": "yes-flagged",
            "R5_confidence": "high",
        },
    )
    halt_handoff.write_halt_entry(
        tmp_slug_dir,
        slug="acceptance_j20b",
        chain_id="chain_2026-05-22T12:00:00Z_j20b0000",
        halt_reason="tampering_detected",
        iteration_records=[record],
    )

    rows = _read_log_rows(tmp_slug_dir)
    assert rows, "halt_handoff emitted no orchestrator_log rows"
    mr_dir = tmp_slug_dir / "morning-review"
    mr_files = list(mr_dir.glob("*.md")) if mr_dir.exists() else []
    assert mr_files, "halt_handoff emitted no morning-review file"
    assert "tampering_detected" in mr_files[0].read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# (c) completion_summary → summary file (LAST gesture per §A.5a)
# ---------------------------------------------------------------------------

def test_audit_completion_summary_emits_file(tmp_slug_dir):
    """J.20c: completion_summary.emit_chain_summary creates a summary .md file."""
    from bin._chain_overnight import completion_summary

    payload = completion_summary.CompletionSummaryInput(
        slug="acceptance_j20c",
        chain_id="chain_2026-05-22T12:00:00Z_j20c0000",
        chain_started_at="2026-05-22T12:00:00Z",
        chain_ended_at="2026-05-22T13:00:00Z",
        halt_reason="success",
        driver_exit_code=0,
        phases=(),
        committed_files=(),
        wall_clock_cap_seconds=43200,
        wall_clock_total_seconds=3600,
        cost_total_usd=2.5,
    )
    completion_summary.emit_chain_summary(plan_dir=tmp_slug_dir, payload=payload)
    summary_files = list(tmp_slug_dir.glob("_completion_summary_*.md"))
    assert len(summary_files) == 1, (
        f"expected exactly 1 completion summary file; got {len(summary_files)}"
    )
    body = summary_files[0].read_text(encoding="utf-8")
    assert payload.chain_id in body, "summary file missing chain_id discriminator"


# ---------------------------------------------------------------------------
# (d) Sealed-paths deny + suppression-block deny → wired to bin/hook-log
# ---------------------------------------------------------------------------

def test_audit_sealed_paths_hook_invokes_bin_hook_log(repo_root):
    """J.20d: sealed-paths.sh shell wrapper invokes bin/hook-log on deny path."""
    hook = repo_root / "hooks" / "sealed-paths.sh"
    text = hook.read_text(encoding="utf-8")
    # The Python backing may carry the hook-log emit; check both.
    py_backing = repo_root / "bin" / "_hooks" / "sealed_paths_hook.py"
    sources = [text]
    if py_backing.exists():
        sources.append(py_backing.read_text(encoding="utf-8"))
    combined = "\n".join(sources)
    assert "hook-log" in combined or "hook_log" in combined, (
        "sealed-paths.sh has no `bin/hook-log` invocation — sealed-path "
        "refusals would never leave a forensic row in hook_log.jsonl, "
        "breaking the audit-trail completeness claim."
    )


def test_audit_suppression_block_hook_invokes_bin_hook_log(repo_root):
    """J.20e: chain-suppression-block.sh wired to bin/hook-log on deny path."""
    hook = repo_root / "hooks" / "chain-suppression-block.sh"
    text = hook.read_text(encoding="utf-8")
    py_backing = repo_root / "bin" / "_hooks" / "chain_suppression_block.py"
    sources = [text]
    if py_backing.exists():
        sources.append(py_backing.read_text(encoding="utf-8"))
    combined = "\n".join(sources)
    assert "hook-log" in combined or "hook_log" in combined, (
        "chain-suppression-block.sh has no `bin/hook-log` invocation — "
        "suppression refusals would never leave a forensic row in "
        "hook_log.jsonl, breaking the audit-trail completeness claim."
    )


# ---------------------------------------------------------------------------
# Cross-cut: at least 4 distinct gates fire log rows in a synthetic chain
# ---------------------------------------------------------------------------

def test_audit_multi_gate_synthetic_chain_emits_all_rows(tmp_slug_dir, monkeypatch):
    """J.20f: fire 3 gates in sequence, confirm orchestrator_log has rows for each.

    This is the highest-leverage assertion — if any single gate gets
    bypassed during a chain run, the operator can't reconstruct the
    halt timeline. Asserts orchestrator_log row counts AFTER each gate
    fires, so a missing row is named precisely.
    """
    from bin._route_issue import fix_now, log_emit
    from bin._retry_loop import halt_handoff
    from bin._retry_loop.iteration_loop import IterationRecord

    monkeypatch.setattr(
        log_emit, "resolve_plan_dir",
        lambda plan_dir, plan_slug: tmp_slug_dir,
    )

    # Gate 1: route_issue fix-now.
    rows_before_g1 = len(_read_log_rows(tmp_slug_dir))
    fix_now.run(
        description="J20f gate1", context="J20f:1",
        dry_run=False, json_output=False, repo_root=None, plan_slug=None,
    )
    rows_after_g1 = len(_read_log_rows(tmp_slug_dir))
    assert rows_after_g1 > rows_before_g1, "gate 1 (route_issue) added no row"

    # Gate 2: halt_handoff tampering.
    rec = IterationRecord(
        iteration_n=1,
        started_at="2026-05-22T12:00:00Z",
        ended_at="2026-05-22T12:01:00Z",
        test_runner_exit_code=1,
        failing_tests=["t.py::t"],
        diff_excerpt="-pass\n+broken\n",
        rubric={"R1_root_cause": "x", "R2_what_missed": "y", "R3_next_action": "z",
                "R4_tampering": "yes-flagged", "R5_confidence": "high"},
    )
    halt_handoff.write_halt_entry(
        tmp_slug_dir, slug="acceptance_j20f",
        chain_id="chain_2026-05-22T12:00:00Z_j20f0000",
        halt_reason="tampering_detected", iteration_records=[rec],
    )
    rows_after_g2 = len(_read_log_rows(tmp_slug_dir))
    assert rows_after_g2 > rows_after_g1, "gate 2 (halt_handoff) added no row"

    # Gate 3: another route_issue call (verifies each fix-now produces its own
    # discrete row, not deduplicated).
    fix_now.run(
        description="J20f gate3", context="J20f:3",
        dry_run=False, json_output=False, repo_root=None, plan_slug=None,
    )
    rows_after_g3 = len(_read_log_rows(tmp_slug_dir))
    assert rows_after_g3 > rows_after_g2, "gate 3 (route_issue again) added no row"

    # Final total: at least 3 rows from 3 gate fires.
    rows = _read_log_rows(tmp_slug_dir)
    assert len(rows) >= 3, (
        f"expected ≥3 orchestrator_log rows from 3 gate fires; got {len(rows)}"
    )
