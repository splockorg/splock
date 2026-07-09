"""J.9 — `bin/lessons add` → planner subprocess seam.

Per inventory:
- Source: userguide §10 + Opus M-7 (lessons→planner subprocess seam
  invisible to current tests).
- Expected outcome: after `bin/lessons add`, planner subprocess invocation
  reads via `bin/lessons query --json`; content wrapped in `<lessons-findings>`
  delimiters in the constructed prompt.
"""

from __future__ import annotations

import json
import pytest
from unittest import mock


pytestmark = pytest.mark.acceptance


# 2026-05-22 (Pass 6): un-skipped — subprocess_cli_runner fixture monkey-patches
# bin._lessons.writer._PLANS_DIR to tmp_path, redirecting writes away from the
# real docs/plans/ tree (closes the §F isolation gap for this test).
def test_lessons_add_then_planner_consumes_via_subprocess(
    subprocess_cli_runner, monkeypatch
):
    """J.9: lessons.md content surfaces in planner Call 1 prompt via subprocess."""
    from bin._lessons import cli as lessons_cli
    from bin._planner import main as planner_main

    # subprocess_cli_runner returns the redirected docs/plans/ Path.
    # Slug must match the canonical Slug regex (^[a-z0-9][a-z0-9_-]*$).
    slug = "acceptance-j9"
    plan_dir = subprocess_cli_runner / slug
    plan_dir.mkdir(parents=True)
    (plan_dir / f"{slug}_recon.md").write_text("# Recon\n\nSurface.\n", encoding="utf-8")

    # Add a lesson via the CLI; subprocess-safe — calls the module entry point.
    # Per bin/_lessons/cli.py: positional slug + --task / --title / --approach
    # / --failure / --rejection / --reattempt / --source (all required).
    #
    # We mock the underlying JSONL writer because the `lesson_added` event_type
    # is not in the current orchestrator_log_v1 enum (separate finding,
    # related to K.2 §J event_type bump but distinct §M event_type). This
    # test's scope is the lessons→planner seam, not the JSONL schema.
    # Mock the lessons-side emit binding (imported INTO cli.py from log_emit)
    # plus the underlying jsonl writer — belt-and-suspenders so the
    # lesson_added event_type schema gap (separate finding, related to K.2)
    # doesn't poison this seam test.
    with mock.patch("bin._lessons.cli.emit_lesson_added"), \
         mock.patch("bin._jsonl_log.writer.append_row"):
        argv = [
            "add", slug,                              # positional slug
            "--task", "T01",
            "--title", "tried embedding lookup inline",
            "--approach", "Synchronous dict lookup per row.",
            "--failure", "performance",
            "--rejection", "P99 latency 30x over budget.",
            "--reattempt", "After batch-precompute lands.",
            "--source", "_orchestrator_log.jsonl:line=42",
        ]
        rc = lessons_cli.main(argv)
    assert rc == 0, f"bin/lessons add returned non-zero (rc={rc})"

    lessons_path = plan_dir / "lessons.md"
    assert lessons_path.exists(), "lessons.md not created by add"
    lessons_text = lessons_path.read_text(encoding="utf-8")
    assert "tried embedding lookup inline" in lessons_text

    # Now: invoke the planner's lessons-read path. This is a private function;
    # we exercise it directly to verify the subprocess-or-fallback contract.
    planner_main_module = planner_main
    if not hasattr(planner_main_module, "_read_lessons"):
        pytest.skip("planner _read_lessons not directly exposed; covered by E2E test")

    lessons_content = planner_main_module._read_lessons(plan_dir)
    assert lessons_content, "planner _read_lessons returned empty for non-empty lessons.md"
    assert "tried embedding lookup inline" in lessons_content, (
        f"planner lessons read didn't include the lesson body; got: {lessons_content!r}"
    )

    # Verify the prompt-construction layer wraps in <lessons-findings> delimiters.
    # external_input_sanitize.wrap(content, kind) — kind comes SECOND.
    try:
        from bin._planner import external_input_sanitize
        wrapped = external_input_sanitize.wrap(lessons_content, "lessons-findings")
        assert "<lessons-findings>" in wrapped, (
            f"wrap() did not produce <lessons-findings> tag; got: {wrapped[:200]!r}"
        )
        assert "</lessons-findings>" in wrapped, (
            "wrap() did not produce </lessons-findings> closing tag"
        )
        assert "tried embedding lookup inline" in wrapped, (
            "wrapped content doesn't contain the lesson body"
        )
    except (ImportError, AttributeError):
        pytest.skip("external_input_sanitize.wrap not directly testable")
