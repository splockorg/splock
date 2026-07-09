"""Single-call qa SDK invocation — the core driver-side mechanism.

Per plan §D.8.3 + v2.7 §1.D. One HTTP round-trip per qa pass:

Call (Reasoning + Emission combined):
- NO `output_config`.
- Free-form MD output structured per the deterministic rubric.
- The rubric's block-A/B/C/D taxonomy is enforced *prompt-side*; there
  is no structural-decoding enforcement because qa output is MD, not
  JSON. The qa subagent's prompt body and the rubric byte-stability
  test together provide the equivalent discipline.

Compare with `bin._planner.two_call.invoke_planner`:
- That module makes two distinct `messages.create(...)` calls per
  planning phase (Call 1 free-form + Call 2 schema-constrained).
- This module makes ONE call per qa pass.
- Both use the same `external_input_sanitize.wrap` discipline for
  external content; both use the same Anthropic SDK surface and model
  pin.

The qa module does NOT write the MD output to disk (driver-writes-not-
subagent invariant per plan §D.6 criterion 5). It returns the MD text
to the caller; `main.py` writes via §B's `atomic_write`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

from .prompt_templates import QA_SYSTEM, render_qa_user
from .rubric import build_rubric
from .subject import DEFAULT_SUBJECT, SUBJECT_PLAN, Subject


# ----------------------------------------------------------------------
# Neutral subject-content delimiter (qa_review_target_generalization T5)
# ----------------------------------------------------------------------
#
# The subject body (recon / qna / research / plan artifact) is wrapped in a
# neutral `<subject-under-review>` delimiter instead of the old
# `<recon-findings>` tag, because `/qa` now reviews any of four predecessor
# artifacts (see `bin._qa.subject`), not just recon.
#
# This label INTENTIONALLY leaves the closed `external_input_sanitize.WrapKind`
# enum — it mirrors the `<qa-rubric>` plain-delimiter precedent below in
# `invoke_qa` (a structural delimiter the model can locate, not an
# external-input WrapKind member). The neutral `subject-under-review` label
# is NOT a `WrapKind` member; the content field is `subject_findings` and the
# rendered slot is `{subject}` (subject-agnostic, since `/qa` reviews any of
# four predecessor artifacts, not just recon).
#
# SECURITY (load-bearing — T5 call_sites): leaving the closed enum must NOT
# mean dropping injection-hardening of the subject body. `external_input_
# sanitize.wrap` itself interpolates content verbatim (no per-content
# escaping) and relies on the system-prompt DELIMITER_INSTRUCTION
# (data-not-instructions) for defense; its sibling `_jsonl_log.delimiter.
# wrap_reason` documents the same "verbatim bytes, no escaping" posture. To
# keep the neutral delimiter from being *weaker* than that posture, this
# helper additionally neutralizes any literal `<subject-under-review>` /
# `</subject-under-review>` token embedded in the subject body so a malicious
# (or merely unlucky) artifact cannot forge the closing delimiter and smuggle
# its tail out of the data envelope. The escape is confined to the delimiter
# token itself; all other bytes pass through unchanged (mirroring `wrap`'s
# verbatim treatment of the rest of the content).
SUBJECT_WRAP_LABEL = "subject-under-review"
"""Neutral structural delimiter label for the subject body. A plain string,
NOT a member of `external_input_sanitize.WrapKind` (the closed enum is
reserved for provenance-named external inputs; this is a structural
delimiter, like `<qa-rubric>`)."""


def _escape_subject_delimiter(content: str) -> str:
    """Neutralize any literal `<subject-under-review>` delimiter token inside
    the subject body so it cannot forge the data-envelope boundary.

    Replaces the angle-bracketed delimiter token (opening and closing forms)
    with a visibly-escaped sentinel that is no longer a parseable tag, while
    leaving every other byte of the body verbatim. This is the content-side
    hardening that keeps the neutral label at least as injection-resistant as
    the `external_input_sanitize.wrap` posture it replaces.
    """
    return (
        content
        .replace(f"</{SUBJECT_WRAP_LABEL}>", f"&lt;/{SUBJECT_WRAP_LABEL}&gt;")
        .replace(f"<{SUBJECT_WRAP_LABEL}>", f"&lt;{SUBJECT_WRAP_LABEL}&gt;")
    )


def wrap_subject(content: str) -> str:
    """Wrap the subject body in the neutral `<subject-under-review>` delimiter.

    Same envelope shape as `external_input_sanitize.wrap`
    (`<label>\\n{content}\\n</label>`) so the rendered prompt is line-aligned
    identically, but the body first passes through `_escape_subject_delimiter`
    so an embedded `</subject-under-review>` cannot break out of the envelope.
    """
    return f"<{SUBJECT_WRAP_LABEL}>\n{_escape_subject_delimiter(content)}\n</{SUBJECT_WRAP_LABEL}>"


DEFAULT_QA_MODEL = "claude-opus-4-8"
"""Default model for the qa pass — the concrete latest/best Opus. Used for the
`recon` / `qna` / `research` subjects.

Bumped 4.7 → 4.8 (2026-06-24) so qa runs on the newest/best Opus, same as the
planner. Deliberately a CONCRETE version, not the `opus` family alias: on the
subscription `claude` CLI the `opus` alias resolves to the STALE
`claude-opus-4-7` (verified 2026-06-24), so the alias would pin qa to the older
model. When a newer Opus ships, bump this one constant. Override via
`OVERNIGHT_CHAIN_QA_MODEL` (an explicit pin passes through to the CLI verbatim).
The concrete resolved version is captured into `QaResult.model_id` for forensic
logging."""


PLAN_QA_MODEL = "claude-sonnet-4-6"
"""Cross-family judge for the `plan` subject (qa_review_target_generalization T8).

The concrete latest Sonnet (the `sonnet` CLI alias resolves to this same
`claude-sonnet-4-6` as of 2026-06-24; pinned concretely for symmetry with the
Opus pins and to stay explicit). Kept as a *named, swappable* config value (NOT
a buried `"sonnet"` literal) so it stays monkeypatchable in tests and
future-swappable to a different (even cross-provider) reviewer model without
touching the selection logic (research Rec #2 / Q-E).

Rationale: plans are authored by Opus (the planner runs on the `opus` alias),
so a *same-family* Opus judge reviewing an Opus-authored plan carries a
self-review bias. Routing the `plan` subject to a Sonnet judge mirrors
the Sonnet-judges-Opus posture in `.claude/agents/reviewer.md:57-66`
(cross-family judging against Opus). The other three subjects
(`recon` / `qna` / `research`) are NOT Opus-authored slug artifacts in
the same self-review sense, so they retain the `DEFAULT_QA_MODEL` Opus
default; only `plan` diverges.

NOTE: this is *model selection only*. There is exactly ONE SDK call per
qa pass regardless of subject — no dual-run / aggregator / merge step.
The cross-model-family dual-run + parent-compare idea from the operator
research is explicitly FUTURE work, out of T8 scope."""


QA_MAX_TOKENS = 32000
"""Max tokens for the qa response. The recon is typically 10-40KB; the
qa output is typically 5-30KB. 32k headroom matches the planner's
CALL1_MAX_TOKENS for symmetry."""


logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------

class QaSdkFailed(RuntimeError):
    """Raised when the qa SDK call fails or returns empty content.

    Carries forensic context so the operator can re-run after diagnosing.

    Attributes
    ----------
    detail : str
        Human-readable failure description (SDK exception message,
        empty-response sentinel, etc.).
    last_response : str | None
        The raw response text if the SDK returned something but it
        failed downstream parsing (e.g., empty text block).
    """

    def __init__(
        self,
        *,
        detail: str,
        last_response: str | None = None,
    ) -> None:
        super().__init__(f"qa SDK call failed: {detail}")
        self.detail = detail
        self.last_response = last_response


# ----------------------------------------------------------------------
# Typed I/O
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class QaInputs:
    """Typed bundle for `invoke_qa(...)` inputs.

    The subject body is RAW (not pre-wrapped) — `invoke_qa` wraps it
    via `wrap_subject(...)` (the neutral `<subject-under-review>`
    delimiter) before submitting. `repo_state_summary` is a controlled
    driver-side string and is NOT wrapped (treated as instructions-safe
    per plan §D.3).

    Attributes
    ----------
    subject_findings : str
        Free-form MD content from the subject artifact under review
        (recon / qna / research / plan — selected by `subject` below;
        recon by default). Required to be non-empty; the CLI gate refuses
        an empty/missing subject artifact. This is the CONTENT field,
        distinct from the `subject` SELECTOR field below.
    repo_state_summary : str
        Driver-controlled summary of repo state. NOT wrapped.
    subject : Subject
        The review subject selector — one of `recon` / `qna` / `research`
        / `plan` (a member of `bin._qa.subject.ALL_SUBJECTS`). Set by
        `bin._qa.main._build_inputs` from the `--subject` flag; defaults
        to `recon` so a no-flag invocation is byte-identical to today.
        This is a *selector* field (which artifact kind), distinct from
        the `subject_findings` CONTENT field above. `invoke_qa` reads
        this to pick the per-subject rubric (`build_rubric`) and applies
        the neutral `<subject-under-review>` wrap label to the body.
    directive : str | None
        Optional operator-authored free-text directive (raw, NOT
        pre-wrapped). When None, no `<operator-directive>` block
        appears in the rendered prompt; when a non-None string, the
        prompt template wraps it via
        `external_input_sanitize.wrap(directive, "operator-directive")`
        per std_command_operator_extensions TE + research R4 option 1
        (data-not-instructions trust framing). The 8KB size cap (SC10)
        is enforced upstream in `bin._qa.main` before construction so
        this dataclass remains structurally permissive.
    """

    subject_findings: str
    repo_state_summary: str
    subject: Subject = DEFAULT_SUBJECT
    directive: str | None = None


@dataclass(frozen=True)
class QaResult:
    """Typed result from `invoke_qa(...)`.

    Attributes
    ----------
    qa_md : str
        Free-form qa output (the body that will become `<slug>_qa.md`).
    cost_usd : float
        Best-effort cost from SDK usage metadata (0.0 if unavailable).
    model_id : str
        Resolved model ID (env-var or default).
    attempt_count : int
        Always 1 (single-call; no driver-layer retries).
    """

    qa_md: str
    cost_usd: float
    model_id: str
    attempt_count: int = 1


# ----------------------------------------------------------------------
# SDK client protocol — for DI / mocking
# ----------------------------------------------------------------------

class _MessagesAPI(Protocol):
    def create(self, **kwargs: Any) -> Any: ...
    def stream(self, **kwargs: Any) -> Any: ...


class AnthropicClient(Protocol):
    """Protocol matching the subset of `anthropic.Anthropic` used here."""

    messages: _MessagesAPI


def _default_client() -> AnthropicClient:
    """Construct the default qa SDK client.

    Returns the subscription-billed transport bridge
    (`bin._sdk_bridge.SubscriptionClient`), which routes the qa call through
    `claude_agent_sdk` → the operator's local `claude` CLI subscription,
    instead of the legacy metered `anthropic.Anthropic()` (which read
    `ANTHROPIC_API_KEY` from `.env` and billed the metered API account). The
    bridge implements the same `.messages.stream(...)` context-manager
    protocol (`.text_stream` + `.get_final_message()`) this module uses, so
    `invoke_qa` and the extraction helpers above are unchanged. Imported
    lazily so the module imports cleanly when the SDK is not installed (tests
    inject a mock anyway).

    `/qa` is an interactive adversarial-review tool; subscription billing is
    the correct account for it. See `bin/_sdk_bridge.py`.
    """
    from bin._sdk_bridge import SubscriptionClient  # local — lazy-import

    return SubscriptionClient()


# ----------------------------------------------------------------------
# SDK response extraction helpers (lifted shape from two_call.py)
# ----------------------------------------------------------------------

def _extract_text(message: Any) -> str:
    """Extract free-form text from a `Message` object."""
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if not content:
        return ""

    chunks: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            chunks.append(text)
    return "".join(chunks)


def _extract_cost_usd(message: Any) -> float:
    """Best-effort cost extraction. Returns 0.0 when unavailable."""
    usage = getattr(message, "usage", None)
    if usage is None and isinstance(message, dict):
        usage = message.get("usage")
    if usage is None:
        return 0.0
    cost = getattr(usage, "cost_usd", None)
    if cost is None and isinstance(usage, dict):
        cost = usage.get("cost_usd")
    if isinstance(cost, (int, float)):
        return float(cost)
    return 0.0


def _extract_model_id(message: Any, fallback: str) -> str:
    """Read the resolved model ID from the SDK message."""
    model = getattr(message, "model", None)
    if model is None and isinstance(message, dict):
        model = message.get("model")
    if isinstance(model, str) and model:
        return model
    return fallback


# ----------------------------------------------------------------------
# Public entry: invoke_qa
# ----------------------------------------------------------------------

def select_model(subject: Subject) -> str:
    """Resolve the qa model for ``subject`` (qa_review_target_generalization T8).

    Per-subject, swappable model selection — keyed on the
    :class:`~bin._qa.subject.Subject` selector that `main.py` already
    threads through `QaInputs.subject` (no new plumbing; model resolution
    stays an invoke.py / SDK-call concern):

    - ``plan`` -> :data:`PLAN_QA_MODEL` (the cross-family Sonnet judge —
      a Sonnet reviewing an Opus-authored plan, mirroring
      `.claude/agents/reviewer.md:57-66`).
    - every other subject (``recon`` / ``qna`` / ``research``) ->
      :data:`DEFAULT_QA_MODEL` (the unchanged Opus default).

    Reads the module-level named constants (NOT buried literals) so a
    test or operator can monkeypatch :data:`PLAN_QA_MODEL` /
    :data:`DEFAULT_QA_MODEL` and have the resolution follow. This is
    *selection only* — there is exactly one SDK call per qa pass; no
    aggregator / dual-run / merge step is introduced.
    """
    if subject == SUBJECT_PLAN:
        return PLAN_QA_MODEL
    return DEFAULT_QA_MODEL


def _resolve_model_id(subject: Subject = DEFAULT_SUBJECT) -> str:
    """Resolve the qa model for ``subject``.

    An explicit `OVERNIGHT_CHAIN_QA_MODEL` env override wins for every
    subject (operator pin is load-bearing — same posture as the reviewer
    subagent's `OVERNIGHT_SONNET_REVIEW_MODEL`); otherwise the per-subject
    default comes from :func:`select_model` (``plan`` -> the cross-family
    Sonnet judge, all others -> the Opus default)."""
    env_override = os.environ.get("OVERNIGHT_CHAIN_QA_MODEL")
    if env_override:
        return env_override
    return select_model(subject)


def invoke_qa(
    slug: str,
    inputs: QaInputs,
    *,
    chain_id: str | None = None,
    client: AnthropicClient | None = None,
) -> QaResult:
    """Invoke the single-call qa pass for a slug's subject artifact.

    Parameters
    ----------
    slug : str
        Plan slug (e.g., 'property_based_parser_hardening').
    inputs : QaInputs
        Subject findings + repo state summary.
    chain_id : str | None
        Optional chain id for forensic logging. None for standalone
        `/qa` invocation.
    client : AnthropicClient | None
        Injected SDK client; tests pass a mock. None = use module
        default (`anthropic.Anthropic()`).

    Returns
    -------
    QaResult

    Raises
    ------
    QaSdkFailed
        If the SDK call errors OR returns empty text content.
    """
    if not inputs.subject_findings.strip():
        raise QaSdkFailed(
            detail="empty subject_findings — refusing to spawn qa pass on an "
            "empty subject artifact"
        )

    if client is None:
        client = _default_client()

    # Per-subject model selection (qa_review_target_generalization T8):
    # `plan` routes to the cross-family Sonnet judge (PLAN_QA_MODEL), every
    # other subject keeps the Opus DEFAULT_QA_MODEL. An OVERNIGHT_CHAIN_QA_MODEL
    # env override (handled inside _resolve_model_id) still wins for any
    # subject. This is selection ONLY — exactly one SDK call below regardless
    # of subject; no aggregator / dual-run / merge step.
    model_id = _resolve_model_id(inputs.subject)
    logger.debug(
        "invoke_qa slug=%s chain_id=%s subject=%s model=%s",
        slug, chain_id, inputs.subject, model_id,
    )

    # Rubric is internally-authored (this module's deterministic
    # assembler), NOT external content, so it does not flow through
    # external_input_sanitize.wrap (whose WrapKind enum is reserved for
    # external inputs). Use a plain structural delimiter so the model can
    # locate the rubric boundary in the user prompt. `build_rubric` picks
    # the per-subject rubric deterministically from `inputs.subject`.
    rubric_wrapped = f"<qa-rubric>\n{build_rubric(inputs.subject)}\n</qa-rubric>"
    # Subject body wrapped in the NEUTRAL `<subject-under-review>` delimiter
    # (not `<recon-findings>`) — `/qa` reviews any of four predecessor
    # artifacts, so the label is subject-agnostic. `wrap_subject` mirrors the
    # `<qa-rubric>` plain-delimiter shape above (NOT an `external_input_
    # sanitize.WrapKind` member) while still hardening the body against a
    # forged closing delimiter.
    subject_wrapped = wrap_subject(inputs.subject_findings)

    user_prompt = render_qa_user(
        slug=slug,
        rubric_wrapped=rubric_wrapped,
        subject_wrapped=subject_wrapped,
        repo_state=inputs.repo_state_summary,
        directive=inputs.directive,
    )

    call_kwargs: dict[str, Any] = {
        "model": model_id,
        "max_tokens": QA_MAX_TOKENS,
        "system": QA_SYSTEM,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    # The Anthropic SDK requires streaming for operations that may exceed
    # 10 minutes. With max_tokens=32000 the SDK's worst-case time
    # projection trips this threshold even when the actual response is
    # much shorter. Use `client.messages.stream(...)` and collect the
    # final message; the message object has the same shape as a
    # non-streamed response (content[0].text + usage), so downstream
    # extraction helpers are unchanged.
    try:
        with client.messages.stream(**call_kwargs) as stream:
            for _ in stream.text_stream:
                pass
            msg = stream.get_final_message()
    except Exception as exc:  # noqa: BLE001 — surface SDK errors via QaSdkFailed
        raise QaSdkFailed(detail=f"messages.stream raised: {exc}") from exc

    qa_text = _extract_text(msg)
    if not qa_text.strip():
        raise QaSdkFailed(
            detail="SDK returned empty text content",
            last_response=qa_text,
        )

    cost = _extract_cost_usd(msg)
    model_resolved = _extract_model_id(msg, model_id)

    return QaResult(
        qa_md=qa_text,
        cost_usd=cost,
        model_id=model_resolved,
        attempt_count=1,
    )


__all__ = [
    "AnthropicClient",
    "DEFAULT_QA_MODEL",
    "PLAN_QA_MODEL",
    "QaInputs",
    "QaResult",
    "QaSdkFailed",
    "Subject",
    "invoke_qa",
    "select_model",
]
