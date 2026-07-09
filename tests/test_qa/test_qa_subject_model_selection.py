"""Per-subject swappable model selection for `/qa` (qa_review_target_generalization T8).

`/qa` reviews one of four predecessor artifacts (recon / qna / research /
plan; see `bin._qa.subject`). T8 makes the *judge model* per-subject:

- ``plan`` -> :data:`bin._qa.invoke.PLAN_QA_MODEL` — a CROSS-FAMILY judge
  (Sonnet reviewing an Opus-authored plan), mirroring the
  Sonnet-judges-Opus posture in `.claude/agents/reviewer.md:57-66`.
- ``recon`` / ``qna`` / ``research`` -> :data:`bin._qa.invoke.DEFAULT_QA_MODEL`
  — the UNCHANGED Opus default (bumping it is out of T8 scope).

These tests pin four contracts:

1. ``select_model("plan")`` resolves to the cross-family non-Opus judge
   (equals the named ``PLAN_QA_MODEL`` constant, and is not an Opus model).
2. ``select_model(recon|qna|research)`` each resolve to the unchanged
   ``DEFAULT_QA_MODEL`` (still Opus 4.7).
3. **Overridable via config:** monkeypatching the named ``PLAN_QA_MODEL``
   constant makes ``select_model("plan")`` follow the patched value —
   proving the resolver reads the named config, not a buried ``"sonnet"``
   literal.
4. **Negative — selection ONLY:** the invoke path makes EXACTLY ONE SDK
   call regardless of subject (no model-aggregator / dual-run / merge step
   was introduced). The cross-model-family dual-run + parent-compare idea
   is explicitly FUTURE work, out of T8 scope.
"""

from __future__ import annotations

from typing import Any

import pytest

import bin._qa.invoke as invoke_mod
from bin._qa.invoke import (
    DEFAULT_QA_MODEL,
    PLAN_QA_MODEL,
    QaInputs,
    invoke_qa,
    select_model,
)
from bin._qa.subject import (
    SUBJECT_PLAN,
    SUBJECT_QNA,
    SUBJECT_RECON,
    SUBJECT_RESEARCH,
)


# Subjects that must retain the unchanged Opus default (everything but plan).
_NON_PLAN_SUBJECTS = (SUBJECT_RECON, SUBJECT_QNA, SUBJECT_RESEARCH)


# ----------------------------------------------------------------------
# (1) plan -> cross-family non-Opus judge
# ----------------------------------------------------------------------

def test_select_model_plan_is_cross_family_non_opus_judge() -> None:
    """``select_model("plan")`` resolves to the cross-family judge —
    equals the named ``PLAN_QA_MODEL`` constant and is NOT an Opus model
    (a Sonnet reviewing an Opus-authored plan, mirroring
    `.claude/agents/reviewer.md:57-66`)."""
    resolved = select_model(SUBJECT_PLAN)
    assert resolved == PLAN_QA_MODEL
    # Cross-family: the plan judge must not be an Opus model (the planner
    # pins Opus, so an Opus judge would be same-family self-review).
    assert "opus" not in resolved.lower(), (
        f"plan judge {resolved!r} must be cross-family (non-Opus)"
    )
    # And it must differ from the default Opus judge used for other subjects.
    assert resolved != DEFAULT_QA_MODEL


def test_plan_qa_model_constant_is_a_sonnet_model() -> None:
    """The named plan-judge constant is the current Sonnet pin (a *named*
    swappable config value, not a buried ``"sonnet"`` literal)."""
    assert "sonnet" in PLAN_QA_MODEL.lower()
    assert "opus" not in PLAN_QA_MODEL.lower()


# ----------------------------------------------------------------------
# (2) recon / qna / research -> unchanged DEFAULT_QA_MODEL (Opus)
# ----------------------------------------------------------------------

@pytest.mark.parametrize("subject", _NON_PLAN_SUBJECTS)
def test_select_model_non_plan_subjects_use_default(subject: str) -> None:
    """Every non-plan subject resolves to the unchanged ``DEFAULT_QA_MODEL``
    — only ``plan`` diverges to the cross-family judge."""
    assert select_model(subject) == DEFAULT_QA_MODEL


def test_default_qa_model_is_latest_opus() -> None:
    """``DEFAULT_QA_MODEL`` is the concrete latest/best Opus (recon/qna/research
    keep the Opus default). Bumped 4.7 → 4.8 on 2026-06-24 so qa runs on the
    best Opus, same as the planner; pinned concretely (NOT the `opus` alias,
    which resolves to the stale 4.7 on the subscription CLI)."""
    assert DEFAULT_QA_MODEL == "claude-opus-4-8"


def test_only_plan_diverges_from_default() -> None:
    """Across all four subjects, ``plan`` is the *only* one whose resolved
    model differs from ``DEFAULT_QA_MODEL``."""
    diverging = {
        s
        for s in (SUBJECT_RECON, SUBJECT_QNA, SUBJECT_RESEARCH, SUBJECT_PLAN)
        if select_model(s) != DEFAULT_QA_MODEL
    }
    assert diverging == {SUBJECT_PLAN}


# ----------------------------------------------------------------------
# (3) Overridable via config — monkeypatch the named constant
# ----------------------------------------------------------------------

def test_plan_model_follows_monkeypatched_named_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Monkeypatching the named ``PLAN_QA_MODEL`` constant makes
    ``select_model("plan")`` follow the patched value — proving the
    resolver reads the named config, not a hard-coded ``"sonnet"`` literal
    (research Rec #2 / future cross-provider swap)."""
    sentinel = "some-future-cross-provider/reviewer-model-v9"
    monkeypatch.setattr(invoke_mod, "PLAN_QA_MODEL", sentinel)
    assert select_model(SUBJECT_PLAN) == sentinel
    # Non-plan subjects are unaffected by the plan-judge swap.
    assert select_model(SUBJECT_RECON) == DEFAULT_QA_MODEL


def test_non_plan_subjects_follow_monkeypatched_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Symmetric proof for the default judge: patching the named
    ``DEFAULT_QA_MODEL`` constant makes the non-plan subjects follow it,
    while ``plan`` keeps reading its own named constant."""
    sentinel = "some-other-default/reviewer-model-v9"
    monkeypatch.setattr(invoke_mod, "DEFAULT_QA_MODEL", sentinel)
    for subject in _NON_PLAN_SUBJECTS:
        assert select_model(subject) == sentinel
    # plan still reads PLAN_QA_MODEL, which we did NOT patch here.
    assert select_model(SUBJECT_PLAN) == PLAN_QA_MODEL


# ----------------------------------------------------------------------
# (4) Negative — selection ONLY: exactly one SDK call, no aggregator/merge
# ----------------------------------------------------------------------

class _CountingStream:
    """Minimal context-manager matching `client.messages.stream(...)`."""

    def __init__(self) -> None:
        # Empty text_stream so the `for _ in stream.text_stream` loop in
        # invoke_qa is a no-op.
        self.text_stream: list[str] = []

    def __enter__(self) -> "_CountingStream":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def get_final_message(self) -> Any:
        class _Block:
            text = "# qa\nstub finding body\n"

        class _Msg:
            content = [_Block()]
            usage = None
            model = "stub-model"

        return _Msg()


class _CountingMessages:
    def __init__(self) -> None:
        self.stream_calls = 0
        self.create_calls = 0
        self.models_seen: list[str] = []

    def stream(self, **kwargs: Any) -> _CountingStream:
        self.stream_calls += 1
        self.models_seen.append(kwargs.get("model"))
        return _CountingStream()

    def create(self, **kwargs: Any) -> Any:  # pragma: no cover - unused path
        self.create_calls += 1
        raise AssertionError("invoke_qa must use .stream(), not .create()")


class _CountingClient:
    """Counts how many SDK round-trips invoke_qa makes."""

    def __init__(self) -> None:
        self.messages = _CountingMessages()


def _invoke_for_subject(subject: str) -> _CountingClient:
    client = _CountingClient()
    inputs = QaInputs(
        subject_findings="# body\na load-bearing claim under review\n",
        repo_state_summary="(no repo-state summary provided)",
        subject=subject,
    )
    invoke_qa("example_slug", inputs, client=client)
    return client


@pytest.mark.parametrize(
    "subject", [SUBJECT_RECON, SUBJECT_QNA, SUBJECT_RESEARCH, SUBJECT_PLAN]
)
def test_invoke_makes_exactly_one_sdk_call_per_subject(subject: str) -> None:
    """The invoke path makes EXACTLY ONE SDK call regardless of subject —
    no model-aggregator / dual-run / merge step was introduced. T8 picks
    *which* single model judges; it does not multiply the calls."""
    client = _invoke_for_subject(subject)
    assert client.messages.stream_calls == 1, (
        f"subject={subject}: expected exactly one SDK round-trip, got "
        f"{client.messages.stream_calls} (a dual-run/aggregator would make >1)"
    )
    assert client.messages.create_calls == 0


def test_plan_subject_sends_cross_family_model_in_the_single_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a plan-subject invoke sends ``PLAN_QA_MODEL`` (the
    cross-family judge) as the ``model=`` of its single SDK call, while a
    recon-subject invoke sends ``DEFAULT_QA_MODEL`` — proving the SDK call
    site reads ``select_model(inputs.subject)``."""
    # Neutralize any operator env override so the per-subject default shows.
    monkeypatch.delenv("OVERNIGHT_CHAIN_QA_MODEL", raising=False)

    plan_client = _invoke_for_subject(SUBJECT_PLAN)
    assert plan_client.messages.stream_calls == 1
    assert plan_client.messages.models_seen == [PLAN_QA_MODEL]

    recon_client = _invoke_for_subject(SUBJECT_RECON)
    assert recon_client.messages.stream_calls == 1
    assert recon_client.messages.models_seen == [DEFAULT_QA_MODEL]


def test_no_aggregator_or_merge_symbol_in_invoke_module() -> None:
    """Structural negative guard: the invoke module surface contains no
    aggregator / dual-run / merge entry point. T8 is selection-only; if a
    future task adds a merge step, this guard fails on purpose so the
    selection-only contract is revisited deliberately."""
    public = set(getattr(invoke_mod, "__all__", []))
    banned_fragments = ("aggregat", "merge", "dual_run", "dualrun", "ensemble")
    offenders = {
        name
        for name in dir(invoke_mod)
        if not name.startswith("__")
        and any(frag in name.lower() for frag in banned_fragments)
    }
    assert not offenders, (
        f"invoke module unexpectedly exposes aggregator/merge-like symbols "
        f"{sorted(offenders)} — T8 must remain selection-only (one SDK call)"
    )
    # The public surface is exactly the selection-only set we expect to grow.
    assert "select_model" in public
    assert "PLAN_QA_MODEL" in public
