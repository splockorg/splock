"""Subscription-billed transport bridge for the splock planner + qa CLIs.

Replaces the metered ``anthropic.Anthropic()`` client (which reads
``ANTHROPIC_API_KEY`` from ``.env`` and bills the metered API account) with an
adapter that routes every model call through ``claude_agent_sdk`` — i.e. the
operator's local ``claude`` CLI subscription, the same transport the test-step
retry loop (``bin/_retry_loop/sdk_spawners.py``) already uses for the coder +
reviewer spawns.

Why this exists
---------------
``/plan``, ``/implplan`` and ``/qa`` are interactive code-authoring tools
driving Claude to do dev work — squarely the Claude Code subscription's
intended use, not metered batch/production API traffic. The planner family was
built (2026-05-21) on the Anthropic Python SDK three days *before* the Agent
SDK subscription transport entered the repo (2026-05-24), and was never
retrofitted; this module closes that gap so all of splock bills one account.

What it adapts
--------------
The two CLIs touch only a thin subset of the ``anthropic.Anthropic`` surface,
and both reach it through a ``_default_client()`` seam:

  - ``client.messages.create(**kwargs)`` — used by the two-call planner
    (:func:`bin._planner.two_call.invoke_planner`). Call 1 is free-form; Call 2
    passes ``output_config={"format": {"type": "json_schema", "schema": ...}}``
    for constrained-decoding JSON emission.
  - ``client.messages.stream(**kwargs)`` — used by the single-call qa invoker
    (:func:`bin._qa.invoke.invoke_qa`) as a context manager exposing
    ``.text_stream`` + ``.get_final_message()``.

Both return an object shaped like an Anthropic ``Message``
(``.content[0].text``, ``.model``, ``.usage.cost_usd``, optional ``.subtype``)
so the existing extraction helpers in those modules keep working **unchanged**.

Empirical SDK note (claude-agent-sdk 0.2.87 + Claude Code CLI)
-------------------------------------------------------------
With ``output_format`` set, constrained decoding works (the emitted text is
guaranteed schema-valid) but ``ResultMessage.structured_output`` is NOT
populated — the JSON arrives as a (possibly Markdown-fenced) block in
``ResultMessage.result``. The Call-2 path therefore extracts the JSON object
from ``.result`` and re-serializes it into ``content[0].text``, which is
exactly what the planner's ``_extract_structured_output`` then
``json.loads()``-es. This mirrors
``bin._retry_loop.sdk_spawners._extract_structured_rubric`` /
``_try_extract_json_object``, which solved the same problem for the retry loop.

Model resolution
----------------
The planner's ``_resolve_model_id`` calls ``client.models.list()`` for
auto-latest-Opus discovery. :class:`SubscriptionClient` deliberately exposes no
``.models`` attribute, so that discovery best-effort no-ops and the planner
falls back to its hardcoded ``DEFAULT_PLANNER_MODEL`` (currently
``claude-opus-4-8`` — the latest Opus). An explicit
``OVERNIGHT_CHAIN_PLANNER_MODEL`` / ``OVERNIGHT_CHAIN_QA_MODEL`` pin still wins
(it short-circuits before discovery). The resolved model id is passed straight
to ``ClaudeAgentOptions.model`` (the ``claude`` CLI accepts both full ids like
``claude-opus-4-8`` and family aliases like ``opus``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import pathlib
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

# Metered-account credentials that, if visible to the spawned `claude` CLI,
# make Claude Code bill the metered API account instead of the subscription —
# the exact thing this bridge exists to stop. `bin/plan` / `bin/qa` call
# `load_env_file()`, so `ANTHROPIC_API_KEY` is in `os.environ`, and the Agent SDK
# inherits the parent env into the CLI subprocess
# (`subprocess_cli.py:430-436`). We hide these for the duration of each query.
_METERED_AUTH_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

# The subtype string the planner's `_is_retry_exhaustion` pattern-matches on to
# raise `PlannerEmissionExhausted` (→ exit code 16). We synthesize it on the
# Call-2 (structured) path whenever the subscription transport fails to produce
# a parseable JSON object, so a failed emission lands on the planner's designed
# "re-run" path rather than a raw traceback.
_STRUCTURED_RETRY_SUBTYPE = "error_max_structured_output_retries"


def _repo_root() -> pathlib.Path:
    """Repo root (parent of ``bin/``), used as the spawned CLI's cwd."""
    return pathlib.Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------
# Anthropic-`Message`-shaped return objects
# ----------------------------------------------------------------------

class _TextBlock:
    """Minimal stand-in for an Anthropic ``TextBlock`` content block."""

    __slots__ = ("type", "text")

    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Usage:
    """Minimal stand-in for ``Message.usage`` exposing ``cost_usd``."""

    __slots__ = ("cost_usd",)

    def __init__(self, cost_usd: float) -> None:
        self.cost_usd = cost_usd


class _AdaptedMessage:
    """Minimal stand-in for ``anthropic.types.Message``.

    Exposes only the attributes the planner/qa extraction helpers read:
    ``.content`` (list with one text block), ``.model``, ``.usage.cost_usd``,
    and ``.subtype`` (``None`` unless structured-output retry exhaustion, which
    the planner maps to ``PlannerEmissionExhausted`` → exit 16).
    """

    __slots__ = ("content", "model", "usage", "subtype")

    def __init__(
        self,
        *,
        text: str,
        model: str,
        cost_usd: float,
        subtype: str | None = None,
    ) -> None:
        self.content = [_TextBlock(text)]
        self.model = model
        self.usage = _Usage(cost_usd)
        self.subtype = subtype


# ----------------------------------------------------------------------
# JSON extraction (Call-2 structured path)
# ----------------------------------------------------------------------

def _try_extract_json_object(text: str) -> dict | None:
    """Extract the first complete top-level JSON object from a text blob.

    Returns the parsed dict, or ``None`` if no extractable object is found (or
    the first complete object is not a dict / not valid JSON).

    Walk forward from the first ``{`` tracking brace depth + JSON string state
    (respecting escapes) until depth returns to 0, then ``json.loads`` the
    slice. Tolerant of Markdown fences and leading/trailing prose — the
    schema-bound CLI output is constrained-decoded, so the object itself is
    well-formed. Mirrors
    ``bin._retry_loop.sdk_spawners._try_extract_json_object``.
    """
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    i = start
    while i < len(text):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        return None
                    return parsed if isinstance(parsed, dict) else None
        i += 1
    return None


# ----------------------------------------------------------------------
# Message / result helpers
# ----------------------------------------------------------------------

def _last_user_text(messages: Any) -> str:
    """Pull the user prompt string out of an Anthropic-style ``messages`` list.

    The planner + qa always submit exactly one user message whose ``content``
    is a plain string; tolerant of a content-block list for safety.
    """
    if not messages:
        return ""
    last = messages[-1]
    content = last.get("content") if isinstance(last, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


def _result_text(result_message: Any) -> str:
    """Final free-form text from a terminal ``ResultMessage.result`` field."""
    result = getattr(result_message, "result", None)
    return result if isinstance(result, str) else ""


def _cost_usd(result_message: Any) -> float:
    """``ResultMessage.total_cost_usd`` as a float (0.0 when absent/None)."""
    cost = getattr(result_message, "total_cost_usd", None)
    return float(cost) if isinstance(cost, (int, float)) else 0.0


def _structured_dict(result_message: Any) -> dict | None:
    """Two-source structured payload extraction (see module docstring).

    1. ``ResultMessage.structured_output`` dict — the intended path; if a
       future CLI/SDK release populates it, this fires first.
    2. ``ResultMessage.result`` string with an embedded (possibly fenced)
       JSON object — the actual live behavior on claude-agent-sdk 0.2.87.
    """
    structured = getattr(result_message, "structured_output", None)
    if isinstance(structured, dict):
        return structured
    return _try_extract_json_object(_result_text(result_message))


# ----------------------------------------------------------------------
# SDK driver
# ----------------------------------------------------------------------

@contextlib.contextmanager
def _force_subscription_auth() -> Iterator[None]:
    """Hide metered API credentials from the spawned ``claude`` CLI subprocess.

    Claude Code prefers an inherited ``ANTHROPIC_API_KEY`` (metered billing)
    over the operator's subscription. Since the Agent SDK inherits the parent
    process env, and the planner/qa CLIs ``load_env_file()`` that key into the
    env, we temporarily remove the metered auth vars for the duration of the
    query so the CLI authenticates against the subscription, then restore them
    (other subsystems — e.g. the grouping-policy LLM judges — still use the
    key). Restoration is in a ``finally`` so an exception can't leak the
    stripped state.
    """
    saved: dict[str, str] = {}
    for key in _METERED_AUTH_ENV_VARS:
        if key in os.environ:
            saved[key] = os.environ.pop(key)
    try:
        yield
    finally:
        os.environ.update(saved)


def _drive_query(
    query_fn: Callable[..., Any], *, prompt: str, options: Any
) -> tuple[Any, str | None]:
    """Async-iterate ``query_fn(prompt, options)`` and return
    ``(terminal ResultMessage, resolved_model)``.

    ``resolved_model`` is the CONCRETE model id stamped on the
    ``AssistantMessage`` by the API (e.g. ``claude-opus-4-8`` even when the
    request asked for the ``opus`` family alias), or ``None`` if absent — so
    forensic logging records the real version, not the alias. Messages are
    detected by class name (no SDK import on the test path).

    The query runs under :func:`_force_subscription_auth` so the spawned CLI
    bills the subscription, never the metered key.
    """

    async def _run() -> tuple[Any, str | None]:
        final: Any = None
        resolved_model: str | None = None
        async for msg in query_fn(prompt=prompt, options=options):
            cls = type(msg).__name__
            if cls == "AssistantMessage":
                m = getattr(msg, "model", None)
                if isinstance(m, str) and m:
                    resolved_model = m
            elif cls == "ResultMessage":
                final = msg
        return final, resolved_model

    with _force_subscription_auth():
        return asyncio.run(_run())


# ----------------------------------------------------------------------
# Client surface
# ----------------------------------------------------------------------

class _Messages:
    """Implements the ``client.messages`` subset: ``create`` + ``stream``."""

    def __init__(self, owner: "SubscriptionClient") -> None:
        self._owner = owner

    def _build_options(self, *, model: str, system: str | None, output_format: Any) -> Any:
        options_cls = self._owner._options_cls()
        # NB: `max_turns` is intentionally NOT pinned. The constrained-emission
        # (`output_format`) flow needs more than one turn — a live `claude` CLI
        # rejects `max_turns=1` here with "Reached maximum number of turns (1)".
        # `allowed_tools=[]` already forbids any agentic tool loop, so leaving
        # `max_turns` at the SDK default lets the structured emission complete
        # without opening the door to tool iterations (this mirrors the proven
        # reviewer/coder spawners in `bin/_retry_loop/sdk_spawners.py`, which
        # also set no `max_turns` alongside `output_format`).
        return options_cls(
            model=model,
            system_prompt=system,
            output_format=output_format,
            allowed_tools=[],
            setting_sources=None,
            cwd=str(self._owner._cwd),
        )

    def create(self, **kwargs: Any) -> _AdaptedMessage:
        """Planner transport — a single-completion ``messages.create`` shim.

        Call 1 (no ``output_config``) returns the free-form text. Call 2
        (``output_config`` json-schema) extracts the constrained JSON and
        re-serializes it into ``content[0].text`` so the planner's
        ``_extract_structured_output`` parses it canonically. A structured-call
        failure surfaces as the retry-exhaustion subtype (→ exit 16); any other
        hard error propagates.
        """
        model = kwargs.get("model")
        system = kwargs.get("system")
        prompt = _last_user_text(kwargs.get("messages"))
        output_config = kwargs.get("output_config")
        output_format = (
            output_config.get("format") if isinstance(output_config, dict) else None
        )

        options = self._build_options(
            model=model, system=system, output_format=output_format
        )
        query_fn = self._owner._query_fn()
        final, resolved_model = _drive_query(query_fn, prompt=prompt, options=options)
        # Prefer the concrete version the API resolved the alias to; fall back
        # to what was requested (e.g. an explicit pin) when absent.
        model_out = resolved_model or model

        if final is None:
            raise RuntimeError(
                "claude_agent_sdk query closed without yielding a ResultMessage "
                "(the claude CLI subprocess may have exited abnormally)"
            )

        is_error = bool(getattr(final, "is_error", False))
        cost = _cost_usd(final)

        if output_format is not None:
            # Call 2 (structured emission). Any inability to produce a parseable
            # JSON object → retry-exhaustion subtype so invoke_planner raises
            # PlannerEmissionExhausted (the designed "re-run the emission" path).
            parsed = None if is_error else _structured_dict(final)
            if parsed is None:
                if is_error:
                    logger.debug(
                        "Call-2 structured emission errored: subtype=%r",
                        getattr(final, "subtype", None),
                    )
                return _AdaptedMessage(
                    text="",
                    model=model_out,
                    cost_usd=cost,
                    subtype=_STRUCTURED_RETRY_SUBTYPE,
                )
            return _AdaptedMessage(
                text=json.dumps(parsed), model=model_out, cost_usd=cost
            )

        # Call 1 (free-form reasoning). A hard error here is a transport
        # failure — propagate it (parity with the metered SDK, which raised).
        if is_error:
            raise RuntimeError(
                "claude_agent_sdk query returned is_error=True "
                f"subtype={getattr(final, 'subtype', '')!r}"
            )
        return _AdaptedMessage(
            text=_result_text(final), model=model_out, cost_usd=cost
        )

    def stream(self, **kwargs: Any) -> "_StreamContext":
        """QA transport — a context manager mirroring ``messages.stream``.

        The single qa call is run eagerly on ``__enter__``; ``.text_stream``
        yields the collected text once (qa only drains it) and
        ``.get_final_message()`` returns the adapted message.
        """
        return _StreamContext(self, kwargs)


class _StreamContext:
    """Context-manager stand-in for ``client.messages.stream(...)``."""

    def __init__(self, messages: _Messages, kwargs: dict[str, Any]) -> None:
        self._messages = messages
        self._kwargs = kwargs
        self._text = ""
        self._final: _AdaptedMessage | None = None

    def __enter__(self) -> "_StreamContext":
        model = self._kwargs.get("model")
        system = self._kwargs.get("system")
        prompt = _last_user_text(self._kwargs.get("messages"))
        options = self._messages._build_options(
            model=model, system=system, output_format=None
        )
        query_fn = self._messages._owner._query_fn()
        final, resolved_model = _drive_query(query_fn, prompt=prompt, options=options)
        if final is None:
            raise RuntimeError(
                "claude_agent_sdk qa query closed without yielding a ResultMessage"
            )
        if getattr(final, "is_error", False):
            raise RuntimeError(
                "claude_agent_sdk qa query returned is_error=True "
                f"subtype={getattr(final, 'subtype', '')!r}"
            )
        self._text = _result_text(final)
        self._final = _AdaptedMessage(
            text=self._text, model=resolved_model or model, cost_usd=_cost_usd(final)
        )
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    @property
    def text_stream(self):
        """Yield the collected text once (qa drains this with ``for _ in``)."""
        if self._text:
            yield self._text

    def get_final_message(self) -> _AdaptedMessage:
        assert self._final is not None  # set in __enter__ before this is reached
        return self._final


class SubscriptionClient:
    """Drop-in for ``anthropic.Anthropic()`` that bills the CC subscription.

    Implements only ``.messages.create`` / ``.messages.stream`` (the subset the
    planner + qa CLIs use). Deliberately exposes NO ``.models`` attribute so the
    planner's auto-latest-Opus discovery best-effort no-ops to its hardcoded
    default (see module docstring).

    Parameters
    ----------
    query_fn, options_cls : injectable seams for tests. When ``None`` (the
        production default), ``claude_agent_sdk.query`` / ``ClaudeAgentOptions``
        are lazily imported on first use so this module imports cleanly without
        the SDK installed.
    cwd : working directory for the spawned ``claude`` CLI subprocess; defaults
        to the repo root.
    """

    def __init__(
        self,
        *,
        query_fn: Callable[..., Any] | None = None,
        options_cls: Callable[..., Any] | None = None,
        cwd: pathlib.Path | str | None = None,
    ) -> None:
        self._injected_query_fn = query_fn
        self._injected_options_cls = options_cls
        self._cwd = pathlib.Path(cwd) if cwd is not None else _repo_root()
        self.messages = _Messages(self)

    @staticmethod
    def _import_agent_sdk() -> Any:
        """Import ``claude_agent_sdk``, or explain how to get it.

        A bare ``ModuleNotFoundError`` surfaces at the FIRST model call — long
        after ``bin/plan <slug>`` looked like it was working — and names a
        package the operator never asked for. Since this transport is now the
        default for the planner and qa, say what to install and where.

        Catches ``ImportError`` rather than only ``ModuleNotFoundError``: a
        half-installed SDK whose own imports fail deserves the same guidance,
        and it is the exception a ``sys.modules[...] = None`` test stub raises.
        """
        try:
            import claude_agent_sdk  # local — lazy-import discipline
        except ImportError as exc:
            raise RuntimeError(
                "claude_agent_sdk could not be imported, and it is required: "
                "it is the transport that bills model calls to your `claude` "
                "CLI subscription rather than a metered ANTHROPIC_API_KEY. "
                "Install it into the venv splock activates ($SPLOCK_VENV, "
                "else ./.venv):\n"
                "    pip install -r requirements-sdk.txt\n"
                "See bin/_sdk_bridge.py and ADOPTION.md."
            ) from exc
        return claude_agent_sdk

    def _query_fn(self) -> Callable[..., Any]:
        if self._injected_query_fn is not None:
            return self._injected_query_fn
        return self._import_agent_sdk().query

    def _options_cls(self) -> Callable[..., Any]:
        if self._injected_options_cls is not None:
            return self._injected_options_cls
        return self._import_agent_sdk().ClaudeAgentOptions


__all__ = ["SubscriptionClient"]
