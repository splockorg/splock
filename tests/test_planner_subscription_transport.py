"""The planner's default transport is the subscription bridge, not the API.

`/plan` and `/implplan` are interactive code-authoring tools, so their model
calls belong on the operator's `claude` CLI subscription rather than a metered
`ANTHROPIC_API_KEY`. `two_call._default_client()` therefore returns
`bin._sdk_bridge.SubscriptionClient`, which implements the same
`.messages.create(...)` protocol the planner already used.

Three consequences are pinned here, because each is easy to regress silently:

1. Nothing constructs `anthropic.Anthropic()` any more. The `AnthropicClient`
   Protocol still *describes* that surface — an operator can still inject a
   metered client — but no default path reaches for it.
2. `SubscriptionClient` exposes **no** `.models` attribute, on purpose. The
   planner's auto-latest-Opus discovery calls `client.models.list()`; without
   the attribute that discovery no-ops and the pinned default wins. A future
   `.models` shim would silently re-enable discovery on a transport that cannot
   serve it.
3. Constructing the client does not import `claude_agent_sdk`. The import is
   deferred to query time, so `bin/plan --help` and every unit test still work
   on a machine that has never installed the agent SDK — as this one has not.
"""

from __future__ import annotations

import sys

import pytest

from bin._planner.two_call import (
    DEFAULT_PLANNER_MODEL,
    _default_client,
    _discover_latest_opus,
    _resolve_model_id,
)
from bin._sdk_bridge import SubscriptionClient


def test_default_client_is_the_subscription_bridge() -> None:
    assert isinstance(_default_client(), SubscriptionClient)


def test_constructing_the_client_does_not_import_the_agent_sdk(monkeypatch) -> None:
    """The SDK import is deferred to query time.

    If construction imported it, `bin/plan` would hard-require claude-agent-sdk
    on every invocation — including `--help` — rather than only when a model
    call is actually made.
    """
    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)
    _default_client()
    assert "claude_agent_sdk" not in sys.modules


def test_subscription_client_deliberately_has_no_models_attribute() -> None:
    """Auto-latest discovery must not fire on a transport that cannot serve it."""
    assert not hasattr(_default_client(), "models")


def test_discovery_no_ops_on_a_client_without_models() -> None:
    """`_discover_latest_opus` is exception-safe by contract: None, never a raise.

    `client.models.list()` on an object with no `.models` raises AttributeError.
    """
    assert _discover_latest_opus(_default_client()) is None


def test_model_falls_back_to_the_pinned_default(monkeypatch) -> None:
    monkeypatch.delenv("OVERNIGHT_CHAIN_PLANNER_MODEL", raising=False)
    assert _resolve_model_id(_default_client()) == DEFAULT_PLANNER_MODEL


def test_default_model_is_a_concrete_version_not_the_opus_alias() -> None:
    """The bare `opus` alias resolves to a stale 4.7 on the subscription CLI.

    4.7 degenerated Call 2's late-alphabetical required array fields
    (`success_criteria`, `tasks_skeleton`), producing schema-valid-but-useless
    plans. Pinning a concrete version is what keeps that from silently
    returning.
    """
    assert DEFAULT_PLANNER_MODEL == "claude-opus-4-8"
    assert DEFAULT_PLANNER_MODEL != "opus"


def test_explicit_operator_pin_wins_over_the_default(monkeypatch) -> None:
    monkeypatch.setenv("OVERNIGHT_CHAIN_PLANNER_MODEL", "claude-opus-4-9")
    assert _resolve_model_id(_default_client()) == "claude-opus-4-9"


def test_an_injected_client_with_models_is_still_used_for_discovery(monkeypatch) -> None:
    """The DI seam survives: a metered client injected by an operator still works."""
    monkeypatch.delenv("OVERNIGHT_CHAIN_PLANNER_MODEL", raising=False)

    class _Model:
        def __init__(self, mid: str, created: str) -> None:
            self.id = mid
            self.created_at = created

    class _Page:
        data = [_Model("claude-opus-4-8", "2026-01-01"), _Model("claude-opus-4-9", "2026-06-01")]

    class _Models:
        @staticmethod
        def list(**_kw):
            return _Page()

    class _MeteredClient:
        models = _Models()

    assert _resolve_model_id(_MeteredClient()) == "claude-opus-4-9"


def test_unimportable_agent_sdk_raises_an_actionable_error(monkeypatch) -> None:
    """A bare ImportError names a package the operator never asked for.

    It also surfaces at the FIRST model call, long after `bin/plan <slug>`
    looked like it was working. Say what to install and where.

    `sys.modules[name] = None` is the standard way to make `import name` fail
    regardless of whether the package is installed — so this test does not
    silently depend on the dev venv lacking the SDK, as an earlier version did.
    """
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)
    client = _default_client()

    with pytest.raises(RuntimeError) as ei:
        client.messages.create(
            model=DEFAULT_PLANNER_MODEL,
            max_tokens=8,
            messages=[{"role": "user", "content": "hi"}],
        )

    message = str(ei.value)
    assert "claude_agent_sdk could not be imported" in message
    assert "pip install -r requirements-sdk.txt" in message
    assert "subscription" in message
    # The original import failure is preserved for debugging.
    assert isinstance(ei.value.__cause__, ImportError)


@pytest.mark.parametrize("falsey", ["0", "false", "no"])
def test_discovery_can_be_disabled_by_env(monkeypatch, falsey: str) -> None:
    monkeypatch.delenv("OVERNIGHT_CHAIN_PLANNER_MODEL", raising=False)
    monkeypatch.setenv("PLANNER_MODEL_AUTO_LATEST", falsey)

    class _Boom:
        @property
        def models(self):  # pragma: no cover - must never be reached
            raise AssertionError("discovery ran despite being disabled")

    assert _resolve_model_id(_Boom()) == DEFAULT_PLANNER_MODEL
