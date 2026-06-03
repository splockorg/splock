"""Two-call planner invocation — the core driver-side mechanism.

Per plan §D.1, §D.6 criterion 1, implplan §D.impl.3 lines 2670-2774.

The two-call structural enforcement is implemented by `invoke_planner`
making TWO DISTINCT `messages.create(...)` invocations per planning
phase. Single-turn dual emission is impossible by construction — the
SDK API surface does not expose a "reason then emit" affordance; each
call is a separate HTTP round-trip with its own `output_config.format`.

Call 1 (Reasoning):
- NO `output_config`.
- Free-form MD scratchpad; reasoning quality preserved.

Call 2 (Emission):
- `output_config={"format": {"type": "json_schema", "schema": <fragment>}}`.
- Transcribes Call 1's output verbatim into schema-valid JSON.
- SDK enforces schema via constrained decoding; the response's first text
  block is guaranteed to be valid JSON conforming to the schema.

SDK retry exhaustion: on `error_max_structured_output_retries`, raise
`PlannerEmissionExhausted` carrying attempt count + last attempted JSON
output. Driver halts with exit code 16 per implplan §D.impl.3 line 2762.

The planner module does NOT write JSON files (plan §D.6 criterion 5 —
driver-writes-not-subagent). It returns the JSON dict to the caller;
the caller (chain driver §A.impl OR the standalone CLI in main.py)
writes via §B's atomic_write.

SDK API surface (verified against anthropic 0.104.0 via claude-api skill,
2026-05-21 per §D mid-section review BLOCKER fixes B-1/B-2):
- Structured outputs use `output_config={"format": {"type": "json_schema",
  "schema": ...}}` on `messages.create()`. The legacy `response_format`
  kwarg does not exist in the stable API.
- The returned `Message` does NOT expose a `.structured_output` attribute.
  The JSON payload is the first text block of `message.content` and must
  be parsed with `json.loads(text)`.
- Under subscription-mode pricing (Claude Code Max), there is no
  per-call usage cap; the SDK enforces only its own internal retries
  and the operator-controlled max_tokens kwarg.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from . import schemas
from .external_input_sanitize import wrap
from .prompt_templates import (
    CALL1_SYSTEM,
    CALL2_SYSTEM,
    render_call1_user,
    render_call2_user,
    select_call2_system,
)

# ----------------------------------------------------------------------
# Model pinning — driver-side env-var default
# ----------------------------------------------------------------------
DEFAULT_PLANNER_MODEL = "claude-opus-4-8"
"""Hardcoded fallback model id — the floor when no explicit pin is set and
auto-latest discovery is unavailable.

Per claude-api skill (skill-validated 2026-05-21 per §D mid-section M-2):
use the bare canonical model ID, never a date-suffixed variant. The skill
explicitly says "Use only the exact model ID strings... they are complete
as-is. Do not append date suffixes." Bare `claude-opus-4-8` is the
canonical Opus 4.8 ID.

Bumped 4.7 → 4.8 (2026-05-29): Opus 4.7 systematically degenerated the
late-alphabetical *required* array fields (`success_criteria`,
`tasks_skeleton`) in Call 2's constrained-decoding emission — emitting
single minimal stub items while richly filling the earlier fields —
producing schema-valid-but-useless plans. Opus 4.8 emits them in full.
See `_resolve_model_id` for the auto-latest-Opus resolution that keeps
this from going stale again."""


AUTO_LATEST_OPUS_ENV = "PLANNER_MODEL_AUTO_LATEST"
"""Kill-switch env var. When set to a falsey value ('0'/'false'/'no'/'off'),
`_resolve_model_id` skips live discovery and uses `DEFAULT_PLANNER_MODEL`.
Default (unset) = discovery ON. An explicit `OVERNIGHT_CHAIN_PLANNER_MODEL`
pin always wins regardless of this switch."""


CALL1_MAX_TOKENS = 32000
CALL2_MAX_TOKENS = 32000
# Explicit timeout bypasses the SDK 0.42+ non-streaming-timeout auto-calc
# (`_base_client._calculate_nonstreaming_timeout`), which refuses any call
# whose `expected_time = 3600 * max_tokens / 128_000` exceeds 600s. At
# max_tokens=32000 that ratio yields 900s, tripping the guard. Streaming is
# the SDK's recommended path for long calls, but the two-call surface needs
# the full response shape before structural validation, so we pin a generous
# explicit timeout instead.
SDK_REQUEST_TIMEOUT_S = 1200


logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------

class PlannerEmissionExhausted(RuntimeError):
    """Raised when the SDK exhausts its internal Structured Outputs retries.

    Per plan §D.5 + implplan §D.impl.3 lines 2756-2766: the SDK's internal
    retries are the design's only retry layer. Carries forensic context
    so the morning-review entry can cite the failure point.

    Attributes
    ----------
    attempt_count : int
        Number of attempts the SDK made before exhaustion.
    last_attempt_output : str | None
        The last partial JSON attempt (truncated if very large), if the
        SDK exposed it.
    call1_reasoning_md : str
        Call 1's free-form output (intact). The operator may use this to
        re-do recon/research or fix the schema and re-run.
    """

    def __init__(
        self,
        *,
        attempt_count: int,
        last_attempt_output: str | None,
        call1_reasoning_md: str,
    ) -> None:
        super().__init__(
            f"SDK exhausted Structured Outputs retries after "
            f"{attempt_count} attempt(s)"
        )
        self.attempt_count = attempt_count
        self.last_attempt_output = last_attempt_output
        self.call1_reasoning_md = call1_reasoning_md


# ----------------------------------------------------------------------
# Typed I/O
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class PlannerInputs:
    """Typed bundle for `invoke_planner(...)` inputs.

    All findings strings are RAW (not pre-wrapped) — the driver wraps
    them via `external_input_sanitize.wrap(...)` before submitting to
    Call 1. `repo_state_summary` is a controlled driver-side string and
    is NOT wrapped (treated as instructions-safe per plan §D.3).

    Attributes
    ----------
    recon_findings : str
        Free-form MD content from `<slug>_recon.md` (or empty string).
    qa_findings : str
        Free-form MD content from the qa subagent's output (or empty).
    research_findings : str
        Free-form MD content from `<slug>_research.md` (or empty).
    lessons_findings : str
        Free-form MD content from `lessons.md` (or empty). Per §M.impl.5,
        the planner's Call 1 sees lessons learned from prior plans.
    repo_state_summary : str
        Driver-controlled summary of repo state. NOT wrapped.
    prior_plan_json : str | None
        For implplan step only: the `<slug>_plan.json` content (already
        schema-validated by §B). None for the plan step.
    tier : str
        Work-sizing tier (e.g., 'Tier 1', 'Tier 1.5', 'Tier 2'). Echoed
        into the Call 1 system prompt for context.
    directive : str | None
        Optional operator-authored free-text directive (raw, NOT
        pre-wrapped). When None, no `<operator-directive>` block appears
        in the rendered Call 1 prompt; when a non-None string, the
        prompt template wraps it via
        `external_input_sanitize.wrap(directive, "operator-directive")`
        per std_command_operator_extensions TD + research R4 option 1
        (data-not-instructions trust framing). The 8KB size cap (SC10)
        is enforced upstream in `bin._planner.main` before construction
        so this dataclass remains structurally permissive. Defaults to
        None so pre-TD callers (chain driver + tests that predate this
        field) still construct PlannerInputs without churn.
    """

    recon_findings: str
    qa_findings: str
    research_findings: str
    lessons_findings: str
    repo_state_summary: str
    prior_plan_json: str | None
    tier: str
    directive: str | None = None
    qna_findings: str = ""
    # ^ v2.8: free-form MD from `<slug>_qna.md` (+ numbered variants), wrapped
    # in `<qna-findings>` for Call 1. Placed LAST with a default so pre-v2.8
    # callers (chain driver + tests constructing PlannerInputs without qna)
    # keep working unchanged.


@dataclass(frozen=True)
class PlannerResult:
    """Typed result from `invoke_planner(...)`.

    Attributes
    ----------
    call1_reasoning_md : str
        Free-form Call 1 output (forensic; logged verbatim).
    call2_emitted_json : dict
        Schema-validated JSON dict from Call 2's structured-output payload.
    call1_cost_usd : float
        Best-effort cost for Call 1 (from SDK usage metadata, if available).
    call2_cost_usd : float
        Best-effort cost for Call 2.
    call1_model_id : str
        Resolved model ID used for Call 1 (env-var or default).
    call2_model_id : str
        Same; both calls use the same env-pinned model.
    call1_attempt_count : int
        Always 1 (no driver-layer retries; SDK handles internally).
    call2_attempt_count : int
        Number of SDK Structured Outputs attempts before success
        (typically 1; tracked for forensic value when SDK exposes it).
    """

    call1_reasoning_md: str
    call2_emitted_json: dict
    call1_cost_usd: float
    call2_cost_usd: float
    call1_model_id: str
    call2_model_id: str
    call1_attempt_count: int = 1
    call2_attempt_count: int = 1


# ----------------------------------------------------------------------
# SDK client protocol — for DI / mocking
# ----------------------------------------------------------------------

class _MessagesAPI(Protocol):
    """Protocol covering the `client.messages.create(...)` surface."""

    def create(self, **kwargs: Any) -> Any: ...


class AnthropicClient(Protocol):
    """Protocol matching the subset of `anthropic.Anthropic` we use.

    Dependency-injectable; tests pass a mock implementing this protocol
    instead of touching `anthropic.Anthropic` directly. Real-world
    callers pass `anthropic.Anthropic()` (or None to use the module
    default).
    """

    messages: _MessagesAPI


def _default_client() -> AnthropicClient:
    """Construct the default `anthropic.Anthropic` client.

    Imported lazily so the module imports cleanly when `anthropic` is
    not installed (tests use a mock anyway).
    """
    import anthropic  # type: ignore[import-untyped]

    return anthropic.Anthropic()


# ----------------------------------------------------------------------
# SDK response extraction helpers
# ----------------------------------------------------------------------

def _extract_text(message: Any) -> str:
    """Extract free-form text from a Call 1 `Message` object.

    Anthropic SDK returns `Message.content` as a list of content blocks;
    the first block of type `text` carries the model output. Tolerant of
    dict-shaped fakes (tests use dicts).
    """
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if not content:
        return ""

    # Walk content blocks; concatenate any 'text' blocks we find.
    chunks: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            chunks.append(text)
    return "".join(chunks)


def _extract_structured_output(message: Any) -> dict:
    """Extract the structured-output JSON dict from a Call 2 `Message`.

    Per claude-api skill (validated against anthropic SDK 0.104.0,
    2026-05-21 per §D mid-section BLOCKER B-2): the SDK does NOT expose
    a `.structured_output` attribute on `Message`. With `output_config.
    format` set to `json_schema`, the response's first text block is
    guaranteed valid JSON conforming to the schema — parse it.

    Tolerant of dict-shaped `message['structured_output']` fakes so
    pre-fix test fixtures still load (these are deprecated; new tests
    should mock the canonical content[0].text shape).

    Raises
    ------
    ValueError
        If no text content is extractable or the text is not valid JSON.
    """
    # Test-fixture compat path: some legacy fixtures provide
    # message['structured_output'] directly. Honor it when present so
    # pre-fix tests don't churn; canonical path parses content[0].text.
    legacy = None
    if isinstance(message, dict):
        legacy = message.get("structured_output")
    if isinstance(legacy, dict):
        return legacy

    text = _extract_text(message)
    if not text:
        raise ValueError(
            "Call 2 returned no text content — expected JSON in content[0].text "
            "per output_config.format contract"
        )
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Call 2 text content was not parseable JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            f"Call 2 JSON parsed to non-dict type: {type(parsed).__name__}"
        )
    return parsed


def _extract_cost_usd(message: Any) -> float:
    """Best-effort cost extraction. Returns 0.0 when unavailable.

    Anthropic's `Message` carries `usage` with input/output token counts;
    converting tokens to USD requires a pricing table that lives outside
    this module (per `extraction/CLAUDE.md` cost-tracker integration).
    For forensic logging the chain driver folds in its own cost source;
    here we just expose what the SDK gave us, or 0.0 as a sentinel.
    """
    usage = getattr(message, "usage", None)
    if usage is None and isinstance(message, dict):
        usage = message.get("usage")
    if usage is None:
        return 0.0
    # Prefer SDK-exposed cost field if present.
    cost = getattr(usage, "cost_usd", None)
    if cost is None and isinstance(usage, dict):
        cost = usage.get("cost_usd")
    if isinstance(cost, (int, float)):
        return float(cost)
    return 0.0


def _is_retry_exhaustion(message: Any) -> bool:
    """Detect `ResultMessage.subtype == 'error_max_structured_output_retries'`.

    The SDK may surface this either as:
    - A returned message with `subtype` attribute / dict key, OR
    - An exception caught upstream (handled in `_call_with_retry_detection`).

    Tolerant of dict-shaped fakes for testing.
    """
    subtype = getattr(message, "subtype", None)
    if subtype is None and isinstance(message, dict):
        subtype = message.get("subtype")
    return subtype == "error_max_structured_output_retries"


def _extract_attempt_count(message: Any) -> int:
    """Read `attempt_count` from the SDK message (best-effort).

    Defaults to 1 if unavailable. Tracked for the
    `PlannerEmissionExhausted` payload's forensic field.
    """
    count = getattr(message, "attempt_count", None)
    if count is None and isinstance(message, dict):
        count = message.get("attempt_count")
    if isinstance(count, int) and count > 0:
        return count
    return 1


def _extract_last_attempt_output(message: Any) -> str | None:
    """Read the last-attempted (failed) JSON output, if SDK exposes it."""
    last = getattr(message, "last_attempt_output", None)
    if last is None and isinstance(message, dict):
        last = message.get("last_attempt_output")
    if isinstance(last, str):
        return last
    return None


def _extract_model_id(message: Any, fallback: str) -> str:
    """Read the resolved model ID from the SDK message.

    The SDK stamps the actual model id (e.g., dated alias resolved to
    versioned string) into `Message.model`. Forensic-grade logging needs
    this to detect silent server-side resolution.
    """
    model = getattr(message, "model", None)
    if model is None and isinstance(message, dict):
        model = message.get("model")
    if isinstance(model, str) and model:
        return model
    return fallback


# ----------------------------------------------------------------------
# Public entry: invoke_planner
# ----------------------------------------------------------------------

_FALSEY = frozenset({"0", "false", "no", "off", ""})


def _discover_latest_opus(client: AnthropicClient) -> str | None:
    """Best-effort discovery of the newest Opus model via `models.list()`.

    Returns the id of the most-recently-created model whose id contains
    'opus', or None on ANY failure (no `models` attr — e.g. spec-restricted
    test mocks —, network error, empty list, malformed payload). Fully
    exception-safe by contract: the caller falls back to
    `DEFAULT_PLANNER_MODEL` whenever this returns None, so discovery can
    never harden into a hard dependency.

    The Anthropic models endpoint returns models newest-first, but we sort
    defensively by `created_at` (stringified for a total order that tolerates
    missing/None values) so ordering is not load-bearing.
    """
    try:
        page = client.models.list(limit=100)  # type: ignore[attr-defined]
        models = list(getattr(page, "data", None) or page)
        opus = [m for m in models if "opus" in (getattr(m, "id", "") or "").lower()]
        if not opus:
            return None
        opus.sort(key=lambda m: str(getattr(m, "created_at", "") or ""), reverse=True)
        chosen = getattr(opus[0], "id", None)
        return chosen if isinstance(chosen, str) and chosen else None
    except Exception:  # noqa: BLE001 — discovery is strictly best-effort
        logger.debug("latest-Opus discovery failed; using DEFAULT_PLANNER_MODEL",
                     exc_info=True)
        return None


def _resolve_model_id(client: AnthropicClient | None = None) -> str:
    """Resolve the planner model. Precedence:

    1. Explicit operator pin `OVERNIGHT_CHAIN_PLANNER_MODEL` (always wins —
       disables discovery; the operator chose a specific version).
    2. Auto-latest-Opus discovery via `client.models.list()` (default ON;
       opt out with `PLANNER_MODEL_AUTO_LATEST=0`). Keeps the planner on the
       latest/best Opus without manual constant-chasing — the staleness that
       caused the 4.7 Call-2 stub bug.
    3. `DEFAULT_PLANNER_MODEL` hardcoded fallback.

    Called ONCE per `invoke_planner`; the single resolved id is reused for
    both Call 1 and Call 2, preserving the "both calls use the same model"
    invariant (plan §D.3).
    """
    pinned = os.environ.get("OVERNIGHT_CHAIN_PLANNER_MODEL")
    if pinned:
        return pinned
    auto = os.environ.get(AUTO_LATEST_OPUS_ENV, "1").strip().lower()
    if client is not None and auto not in _FALSEY:
        latest = _discover_latest_opus(client)
        if latest:
            logger.debug("auto-latest-Opus resolved planner model=%s", latest)
            return latest
    return DEFAULT_PLANNER_MODEL


def invoke_planner(
    slug: str,
    step: Literal["plan", "implplan", "amend"],
    inputs: PlannerInputs,
    *,
    chain_id: str | None = None,
    client: AnthropicClient | None = None,
) -> PlannerResult:
    """Invoke the two-call planner for a single planning phase.

    See implplan §D.impl.3 for the full contract. In short:

    1. Call 1 (Reasoning): no `response_format`; free-form MD.
    2. Call 2 (Emission): `response_format` set to plan or implplan
       schema; SDK enforces via constrained decoding.
    3. Return PlannerResult with both call outputs + forensic metadata.

    Parameters
    ----------
    slug : str
        Plan slug (e.g., 'brand_handoff_gate').
    step : 'plan' | 'implplan' | 'amend'
        Which planning phase. Selects the Call-2 schema + system-prompt
        variant. 'plan' -> full plan_v1 emission + CALL2_SYSTEM; 'implplan'
        -> full orchestrator_v1 emission + CALL2_SYSTEM; 'amend' -> a
        SURGICAL PATCH (plan_patch_v1) + CALL2_SYSTEM_PATCH (the third
        schema-selection branch, plan_surgical_amend §SC6 / T6d). In amend
        mode Call 2 emits a keyed op-list against the prior plan rather than
        a full plan; this function still only EMITS the validated patch
        object — applying it (patch_apply, T6e) and the atomic write/render
        (T6f) are downstream and out of this function's scope.
    inputs : PlannerInputs
        Bundle of recon/qa/research/lessons findings + repo state +
        optional prior plan JSON + tier.
    chain_id : str | None
        Optional chain id for forensic logging. None for standalone
        `/plan` invocation.
    client : AnthropicClient | None
        Injected SDK client; tests pass a mock. None = use module
        default (`anthropic.Anthropic()`).

    Returns
    -------
    PlannerResult

    Raises
    ------
    PlannerEmissionExhausted
        If SDK Structured Outputs retries exhausted. Caller maps to exit 16.
    ValueError
        On structurally-impossible SDK responses (missing structured_output
        on a non-retry-exhausted result, etc.).
    """
    if step not in ("plan", "implplan", "amend"):
        # NB: the leading "step must be 'plan' or 'implplan'" substring is kept
        # verbatim so the existing reject-invalid-step regex test (which
        # predates the amend mode) stays green; 'amend' is appended, not
        # substituted, keeping the message accurate for all three modes.
        raise ValueError(
            f"step must be 'plan' or 'implplan' or 'amend'; got {step!r}"
        )

    if client is None:
        client = _default_client()

    model_id = _resolve_model_id(client)
    logger.debug(
        "invoke_planner slug=%s step=%s chain_id=%s model=%s",
        slug, step, chain_id, model_id,
    )

    # --- Wrap external inputs (per plan §D.3 / implplan §D.impl.6) ---
    recon_wrapped = wrap(inputs.recon_findings, "recon-findings")
    qa_wrapped = wrap(inputs.qa_findings, "qa-findings")
    research_wrapped = wrap(inputs.research_findings, "research-findings")
    qna_wrapped = wrap(inputs.qna_findings, "qna-findings")
    lessons_wrapped = wrap(inputs.lessons_findings, "lessons-findings")

    # --- Call 1 (Reasoning) — NO response_format ---
    # Per std_command_operator_extensions TD: the operator's optional
    # `--directive` flows here. The raw directive is NOT pre-wrapped;
    # `render_call1_user` wraps it via
    # `external_input_sanitize.wrap(directive, "operator-directive")` so
    # the wrap discipline is single-sourced. The 8KB size cap is enforced
    # upstream in `bin._planner.main` per SC10 before PlannerInputs is
    # constructed, so this module does NOT re-validate length.
    call1_user = render_call1_user(
        slug=slug,
        recon=recon_wrapped,
        qa=qa_wrapped,
        research=research_wrapped,
        qna=qna_wrapped,
        lessons=lessons_wrapped,
        repo_state=inputs.repo_state_summary,
        prior_plan=inputs.prior_plan_json,
        step=step,
        directive=inputs.directive,
    )
    call1_system = CALL1_SYSTEM.format(step=step, tier=inputs.tier)

    call1_kwargs: dict[str, Any] = {
        "model": model_id,
        "max_tokens": CALL1_MAX_TOKENS,
        "system": call1_system,
        "messages": [{"role": "user", "content": call1_user}],
        "timeout": SDK_REQUEST_TIMEOUT_S,
    }

    call1_msg = client.messages.create(**call1_kwargs)

    if _is_retry_exhaustion(call1_msg):
        # Call 1 has no response_format; this branch is rare but defensive.
        raise PlannerEmissionExhausted(
            attempt_count=_extract_attempt_count(call1_msg),
            last_attempt_output=_extract_last_attempt_output(call1_msg),
            call1_reasoning_md="",
        )

    call1_text = _extract_text(call1_msg)
    call1_cost = _extract_cost_usd(call1_msg)
    call1_model_resolved = _extract_model_id(call1_msg, model_id)
    call1_attempts = _extract_attempt_count(call1_msg)

    # --- Call 2 (Emission) — response_format set ---
    # Schema-selection branch: which schema fragment goes into Call 2's
    # `output_config.format.schema`, paired with the matching Call-2 system
    # prompt. THREE mutually-exclusive modes — the amend branch is added in
    # PARALLEL CONSTRUCTION (it mirrors, and does not refactor, the two
    # pre-existing full-emission branches):
    #   - plan      -> full plan_v1          + CALL2_SYSTEM
    #   - implplan  -> full orchestrator_v1  + CALL2_SYSTEM
    #   - amend     -> SURGICAL plan_patch_v1 + CALL2_SYSTEM_PATCH  (T6d)
    #
    # The plan/implplan arm below is byte-for-byte UNCHANGED from before this
    # branch existed: the same `schemas.PLAN_SCHEMA_V1 if step=='plan' else
    # schemas.IMPLPLAN_SCHEMA_V1` ternary and the same
    # `CALL2_SYSTEM.format(step=step)` expression (referencing this module's
    # CALL2_SYSTEM name so DI/test substitution of either schema OR the
    # full-emission prompt is honored exactly as before).
    #
    # The amend arm (plan_surgical_amend §SC6 / T6d) pairs
    # PLAN_PATCH_SCHEMA_V1 with the patch-mode system prompt from T6c via
    # `select_call2_system(amend=True)`, so Call 2 emits a keyed op-list
    # (plan_patch_v1), NOT a full plan. invoke_planner still only EMITS the
    # validated patch object here; applying it (patch_apply, T6e) and the
    # atomic write/render (T6f) are downstream. The patch schema is read via
    # the `schemas` module attribute (parity with the full schemas) so a
    # test/DI substitution of the constant is honored.
    if step == "amend":
        schema_fragment = schemas.PLAN_PATCH_SCHEMA_V1
        call2_system = select_call2_system(amend=True).format(step=step)
    else:
        schema_fragment = (
            schemas.PLAN_SCHEMA_V1 if step == "plan" else schemas.IMPLPLAN_SCHEMA_V1
        )
        call2_system = CALL2_SYSTEM.format(step=step)
    call1_wrapped = wrap(call1_text, "call1-reasoning")
    call2_user = render_call2_user(
        schema_inline=json.dumps(schema_fragment, indent=2),
        call1_wrapped=call1_wrapped,
    )

    # Per §D mid-section BLOCKER B-1 (skill-validated): the correct SDK
    # 0.104.0 surface for constrained-decoding output is
    # `output_config={"format": {"type": "json_schema", "schema": ...}}`,
    # NOT `response_format={...}`. The latter does not exist on
    # messages.create() and would raise TypeError on every live call.
    call2_kwargs: dict[str, Any] = {
        "model": model_id,
        "max_tokens": CALL2_MAX_TOKENS,
        "system": call2_system,
        "messages": [{"role": "user", "content": call2_user}],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": schema_fragment,
            },
        },
        "timeout": SDK_REQUEST_TIMEOUT_S,
    }

    call2_msg = client.messages.create(**call2_kwargs)

    if _is_retry_exhaustion(call2_msg):
        raise PlannerEmissionExhausted(
            attempt_count=_extract_attempt_count(call2_msg),
            last_attempt_output=_extract_last_attempt_output(call2_msg),
            call1_reasoning_md=call1_text,
        )

    call2_dict = _extract_structured_output(call2_msg)
    call2_cost = _extract_cost_usd(call2_msg)
    call2_model_resolved = _extract_model_id(call2_msg, model_id)
    call2_attempts = _extract_attempt_count(call2_msg)

    return PlannerResult(
        call1_reasoning_md=call1_text,
        call2_emitted_json=call2_dict,
        call1_cost_usd=call1_cost,
        call2_cost_usd=call2_cost,
        call1_model_id=call1_model_resolved,
        call2_model_id=call2_model_resolved,
        call1_attempt_count=call1_attempts,
        call2_attempt_count=call2_attempts,
    )


__all__ = [
    "AnthropicClient",
    "DEFAULT_PLANNER_MODEL",
    "PlannerEmissionExhausted",
    "PlannerInputs",
    "PlannerResult",
    "invoke_planner",
]
