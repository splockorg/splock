"""K9 — reviewer spawn never receives inject; inject preserved for next consumer.

Per CCOR.1 implplan §T-9 + design_resolutions R-inject-wiring +
userguide §3.6.

Contract:
- The reviewer spawn site in `bin/_retry_loop/iteration_loop.py` is
  structured-output-only deterministic; the inject contract is NOT
  defined for verdict-only prompts.
- `run_iteration`'s reviewer-spawn call does NOT receive `inject_text`.
- The `_consume_operator_inject_iter` helper is called BEFORE the opus
  spawn — never after, never for the reviewer.

This test exercises the source contract AND a behavioural integration
where a staged inject reaches opus but does NOT appear in the reviewer's
prompt.
"""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.acceptance


def _make_completed_process(returncode: int) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode,
        stdout="FAILED tests/x::test_y", stderr="",
    )


def _stage_operator_inject(plan_dir: Path, text: str) -> Path:
    framed = (
        "<!-- operator-inject schema=1 written_at=2026-05-24T22:30:00Z -->\n"
        f"<operator-inject>\n{text}\n</operator-inject>\n"
    )
    target = plan_dir / "_operator_inject.md"
    target.write_text(framed, encoding="utf-8")
    return target


def test_K9_run_iteration_reviewer_call_has_no_inject_text_kwarg():
    """Source contract: `spawn_reviewer_fn(...)` is invoked WITHOUT
    `inject_text` as a kwarg.
    """
    from bin._retry_loop import iteration_loop

    src = inspect.getsource(iteration_loop.run_iteration)
    assert "spawn_reviewer_fn(" in src
    idx = src.index("rubric_payload = spawn_reviewer_fn(")
    end = src.index(")", idx)
    call_block = src[idx:end]
    assert "inject_text" not in call_block, (
        "reviewer spawn must NOT receive inject_text per userguide §3.6"
    )


def test_K9_consume_helper_called_only_before_opus_spawn():
    """Source contract: `_consume_operator_inject_iter` is called once,
    before the opus spawn — never again afterward (e.g., before reviewer).
    """
    from bin._retry_loop import iteration_loop

    src = inspect.getsource(iteration_loop.run_iteration)
    assert "_consume_operator_inject_iter" in src

    consume_idx = src.index(
        "iter_inject_text = _consume_operator_inject_iter("
    )
    reviewer_call_idx = src.index("rubric_payload = spawn_reviewer_fn(")
    assert consume_idx < reviewer_call_idx

    post_reviewer = src[reviewer_call_idx:]
    assert "_consume_operator_inject_iter" not in post_reviewer, (
        "consume helper must NOT be called after the reviewer spawn site"
    )


def test_K9_reviewer_receives_no_inject_end_to_end(tmp_path):
    """With a staged inject, opus receives it but reviewer's prompt does NOT."""
    from bin._retry_loop import iteration_loop

    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    chain_id = "chain_2026-05-24T22:00:00Z_k9k9k9k9"
    slug = "test-chain-slug"
    _stage_operator_inject(plan_dir, "K9 secret-inject-body")

    opus_calls: list[dict] = []
    reviewer_calls: list[dict] = []

    def _fake_opus(*, plan_dir, slug, chain_id, iteration_n, prior_diagnosis,
                   inject_text=None):
        opus_calls.append({"inject_text": inject_text})
        return {
            "diff_excerpt": "+",
            "test_files_edited": [],
            "diff_lines_added": 0,
            "diff_lines_removed": 0,
            "cost_usd": 0.0,
        }

    def _fake_verify(*, plan_dir, iteration_n):
        return _make_completed_process(1)  # force reviewer path

    def _fake_reviewer(*, plan_dir, prompt, rubric_kind):
        reviewer_calls.append({"prompt": prompt, "rubric_kind": rubric_kind})
        return {
            "rubric_version": 1, "iteration": 1,
            "R1_root_cause": "x", "R2_what_missed": "y",
            "R3_next_action": "z", "R4_tampering": "no",
            "R5_confidence": "high",
            "_metadata": {
                "test_files_edited_this_iteration": [],
                "test_runner_exit_code": 1,
                "iteration_diff_lines_added": 0,
                "iteration_diff_lines_removed": 0,
            },
        }

    iteration_loop.run_test_step_loop(
        plan_dir,
        slug=slug, chain_id=chain_id,
        spawn_opus_fn=_fake_opus,
        run_verify_fn=_fake_verify,
        spawn_reviewer_fn=_fake_reviewer,
        max_retries=1,
    )

    # Opus saw the inject.
    assert len(opus_calls) == 1
    assert opus_calls[0]["inject_text"] is not None
    assert "K9 secret-inject-body" in opus_calls[0]["inject_text"]

    # Reviewer was called but the inject content does NOT appear.
    assert len(reviewer_calls) == 1
    reviewer_prompt = reviewer_calls[0]["prompt"]
    assert "K9 secret-inject-body" not in reviewer_prompt
    assert "<operator-inject>" not in reviewer_prompt
