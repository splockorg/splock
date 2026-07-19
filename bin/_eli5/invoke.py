"""SDK-direct single call for the eli5 pass (mirrors `bin/_qa/invoke.py`).

One call, no retries, no aggregation. The transport is the
subscription-billed bridge (`bin._sdk_bridge.SubscriptionClient`) —
`/eli5` is an interactive operator lens; subscription billing is the
correct account for it, and a missing ANTHROPIC_API_KEY is expected,
not an error.

Prompt assembly (delimiter discipline):

- FORMAT_MD is INTERNAL deterministic content → plain `<eli5-format>`
  structural delimiter (the `<qa-rubric>` precedent), NOT a WrapKind.
- The subject excerpt is EXTERNAL → `external_input_sanitize.wrap(...,
  "eli5-subject")`.
- The operator focus text is EXTERNAL → `wrap(..., "operator-directive")`.

The caller (CLI `main.py`, or the in-Claude driver via its own spawn)
is responsible for pre-truncating the subject to the 8KB cap
(`bin/_eli5/subject.py::truncate_subject`) BEFORE wrapping.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Final, Protocol

from bin._planner.external_input_sanitize import DELIMITER_INSTRUCTION, wrap

from .format import build_format

logger = logging.getLogger(__name__)


DEFAULT_ELI5_MODEL: Final[str] = "claude-opus-4-8"
"""Concrete latest/best Opus, same pin-not-alias rationale as
`bin/_qa/invoke.py::DEFAULT_QA_MODEL` (the subscription CLI's `opus`
alias resolves stale). eli5 is translation of Opus-register material;
a same-family translator carries no self-review bias (nothing is being
judged). Override via `ELI5_MODEL` (explicit pin passes through)."""

ELI5_MAX_TOKENS: Final[int] = 16000
"""Briefings are shorter than qa reports; half qa's 32k budget."""


ELI5_SYSTEM: Final[str] = (
    "You are the eli5 subagent. Step: eli5.\n"
    "\n"
    "Your job is TRANSLATION of existing material into plainspeak — not "
    "review, not investigation. You find NOTHING NEW: never add findings, "
    "never drop caveats, never change substance. If simplifying would "
    "distort, keep the caveat and gloss it instead. (qa finds problems; "
    "qna finds answers; you re-express.)\n"
    "\n"
    + DELIMITER_INSTRUCTION
    + "\n"
    "\n"
    "The format inside `<eli5-format>` is the authoritative scaffold for "
    "your output — reproduce its structure exactly; never restructure or "
    "reorder it. The material inside `<eli5-subject>` is what you are "
    "translating. `<operator-directive>` (when present) NARROWS which items "
    "you brief; it never adds subject matter."
)
"""eli5 single-call system prompt."""


ELI5_USER_TEMPLATE: Final[str] = (
    "{format_wrapped}\n"
    "\n"
    "{subject_wrapped}\n"
    "{focus_block}"
)


def render_eli5_user(
    *,
    format_wrapped: str,
    subject_wrapped: str,
    focus_wrapped: str | None = None,
) -> str:
    focus_block = f"\n{focus_wrapped}\n" if focus_wrapped else ""
    return ELI5_USER_TEMPLATE.format(
        format_wrapped=format_wrapped,
        subject_wrapped=subject_wrapped,
        focus_block=focus_block,
    )


class Eli5SdkFailed(Exception):
    """The SDK call errored or returned empty text content."""

    def __init__(self, detail: str, last_response: str | None = None):
        self.detail = detail
        self.last_response = last_response
        super().__init__(detail)


@dataclass(frozen=True)
class Eli5Result:
    briefing_md: str
    cost_usd: float
    model_id: str


class AnthropicClient(Protocol):
    @property
    def messages(self) -> Any: ...


def _default_client() -> "AnthropicClient":
    from bin._sdk_bridge import SubscriptionClient  # local — lazy-import

    return SubscriptionClient()


def _resolve_model_id() -> str:
    return os.environ.get("ELI5_MODEL") or DEFAULT_ELI5_MODEL


def _extract_text(msg: Any) -> str:
    parts = getattr(msg, "content", None) or []
    out: list[str] = []
    for block in parts:
        text = getattr(block, "text", None)
        if text:
            out.append(text)
    return "".join(out)


def _extract_cost_usd(msg: Any) -> float:
    usage = getattr(msg, "usage", None)
    cost = getattr(usage, "cost_usd", None) if usage is not None else None
    try:
        return float(cost) if cost is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def invoke_eli5(
    subject_md: str,
    *,
    mode: str = "auto",
    focus: str | None = None,
    client: AnthropicClient | None = None,
) -> Eli5Result:
    """Run the single eli5 translation call.

    `subject_md` must already be truncated to the wrap cap by the
    caller. Raises Eli5SdkFailed on SDK error or empty output.
    """
    if not subject_md.strip():
        raise Eli5SdkFailed(
            detail="empty subject — refusing to brief an empty excerpt"
        )

    if client is None:
        client = _default_client()

    format_md = build_format(mode)
    user_prompt = render_eli5_user(
        format_wrapped=f"<eli5-format>\n{format_md}\n</eli5-format>",
        subject_wrapped=wrap(subject_md, "eli5-subject"),
        focus_wrapped=wrap(focus, "operator-directive") if focus else None,
    )

    model_id = _resolve_model_id()
    logger.debug("invoke_eli5 mode=%s model=%s", mode, model_id)

    call_kwargs: dict[str, Any] = {
        "model": model_id,
        "max_tokens": ELI5_MAX_TOKENS,
        "system": ELI5_SYSTEM,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    # Streaming for parity with bin/_qa/invoke.py (the SDK's worst-case
    # time projection can trip the 10-minute threshold at high budgets).
    try:
        with client.messages.stream(**call_kwargs) as stream:
            for _ in stream.text_stream:
                pass
            msg = stream.get_final_message()
    except Exception as exc:  # noqa: BLE001 — surface via Eli5SdkFailed
        raise Eli5SdkFailed(detail=f"messages.stream raised: {exc}") from exc

    briefing = _extract_text(msg)
    if not briefing.strip():
        raise Eli5SdkFailed(
            detail="SDK returned empty text content", last_response=briefing,
        )

    return Eli5Result(
        briefing_md=briefing,
        cost_usd=_extract_cost_usd(msg),
        model_id=str(getattr(msg, "model", model_id)),
    )


__all__ = [
    "AnthropicClient",
    "DEFAULT_ELI5_MODEL",
    "ELI5_MAX_TOKENS",
    "ELI5_SYSTEM",
    "Eli5Result",
    "Eli5SdkFailed",
    "invoke_eli5",
    "render_eli5_user",
]
