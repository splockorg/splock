"""IJ.5 — Two-call planner: structural (AST) + runtime (SDK mock) verification.

Per inventory + userguide §1.5 / §3.4 + plan §D.1 + implplan §D.impl.3:
the planner MUST make exactly two `messages.create(...)` calls per
planning step. Single-turn dual emission is impossible by construction.
Defeating this discipline would erase the 10-17% reasoning quality
preservation that the two-call split buys.

This combines two prior gap-finder items (S-9 AST + O-10 SDK mock) into
ONE test file since they verify the same load-bearing structural claim
at different altitudes:

  (a) AST walk (I-class structural): parse `bin/_planner/two_call.py`
      and assert exactly two `client.messages.create(...)` call sites.
      Prevents "I'll just combine them into one call" refactors.
  (b) SDK mock (J-class runtime): monkey-patch the SDK client; invoke a
      full planner step; assert exactly 2 invocations occurred. Call 1
      has NO `output_config`; Call 2 carries `output_config.format.type
      == "json_schema"` pointing at the plan/implplan schema fragment.
"""

from __future__ import annotations

import ast
import pytest
from pathlib import Path


pytestmark = pytest.mark.acceptance


# ---------------------------------------------------------------------------
# (a) Structural / AST — exactly two messages.create call sites
# ---------------------------------------------------------------------------

def _collect_messages_create_callsites(source: str) -> list[tuple[int, str]]:
    """Walk an AST; return [(lineno, callsite_repr)] for every `*.messages.create(...)` call."""
    tree = ast.parse(source)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Looking for X.messages.create(...) — an Attribute chain whose
        # tail is `.create` and whose immediate parent is `.messages`.
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "create":
            continue
        parent = func.value
        if not isinstance(parent, ast.Attribute) or parent.attr != "messages":
            continue
        # Reconstruct a textual representation for the assertion message.
        try:
            repr_str = ast.unparse(func)
        except Exception:  # noqa: BLE001
            repr_str = "<messages.create>"
        hits.append((node.lineno, repr_str))
    return hits


def test_two_call_py_has_exactly_two_messages_create_callsites(repo_root):
    """IJ.5a (structural): bin/_planner/two_call.py contains exactly 2 messages.create calls."""
    src_path = repo_root / "bin" / "_planner" / "two_call.py"
    source = src_path.read_text(encoding="utf-8")
    sites = _collect_messages_create_callsites(source)

    assert len(sites) == 2, (
        f"Expected exactly 2 `*.messages.create(...)` call sites in "
        f"bin/_planner/two_call.py; got {len(sites)}.\n"
        f"Sites: {sites}\n\n"
        "Per plan §D.1 + userguide §1.5: the two-call structural defense "
        "depends on two DISTINCT SDK round-trips. If a refactor reduces "
        "this to one (or splits to three+), the reasoning-quality vs "
        "constrained-decoding tradeoff design no longer holds."
    )


def test_two_call_py_does_not_share_kwargs_between_calls(repo_root):
    """IJ.5b: the kwargs dicts for the two calls are constructed independently.

    A refactor that builds ONE shared kwargs dict and mutates it
    between calls would technically still emit 2 calls but would erase
    the structural argument that "Call 1 has no output_config" is an
    invariant. We assert two distinct local-variable kwargs dicts
    (`call1_kwargs` + `call2_kwargs`) appear in source.
    """
    source = (repo_root / "bin" / "_planner" / "two_call.py").read_text(encoding="utf-8")
    assert "call1_kwargs" in source, "missing `call1_kwargs` local in two_call.py"
    assert "call2_kwargs" in source, "missing `call2_kwargs` local in two_call.py"


# ---------------------------------------------------------------------------
# (b) Runtime / SDK mock — exactly 2 invocations, Call 1 without output_config
# ---------------------------------------------------------------------------

class _MockMessage:
    """Minimal SDK Message mock for Call 1 (reasoning) or Call 2 (emission)."""

    def __init__(self, *, kind: str, model: str = "claude-opus-4-7"):
        self.model = model
        self.attempt_count = 1
        # Call 1: free-form text. Call 2: text containing valid JSON.
        if kind == "call1":
            self.content = [type("Block", (), {"text": "reasoning text"})()]
        else:
            self.content = [type("Block", (), {"text": '{"foo": "bar"}'})()]
        self.usage = type("Usage", (), {"cost_usd": 0.001})()


class _CallCounter:
    """Mock `client.messages.create(...)` that records every invocation."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        kind = "call2" if "output_config" in kwargs else "call1"
        self.calls.append(dict(kwargs))
        return _MockMessage(kind=kind, model=kwargs.get("model", "claude-opus-4-7"))


class _MockClient:
    """Anthropic-shaped mock: `client.messages.create(...)`."""

    def __init__(self):
        counter = _CallCounter()
        self.messages = type("Messages", (), {"create": counter})()
        self._counter = counter

    @property
    def calls(self):
        return self._counter.calls


def test_invoke_planner_makes_exactly_two_sdk_calls(tmp_slug_dir, monkeypatch):
    """IJ.5c (runtime): one invoke_planner call → exactly 2 messages.create invocations."""
    from bin._planner import two_call

    client = _MockClient()
    inputs = two_call.PlannerInputs(
        recon_findings="recon content",
        qa_findings="qa content",
        research_findings="research content",
        lessons_findings="lessons content",
        repo_state_summary="main @ abc123",
        prior_plan_json=None,
        tier="Tier 1",
    )
    result = two_call.invoke_planner(
        slug="acceptance_ij5",
        step="plan",
        inputs=inputs,
        chain_id="chain_2026-05-22T12:00:00Z_ij5c0000",
        client=client,
    )

    assert len(client.calls) == 2, (
        f"invoke_planner should make exactly 2 SDK calls per planning step "
        f"(Call 1 reasoning + Call 2 emission); got {len(client.calls)}"
    )
    # Call 1: NO output_config (free-form reasoning).
    assert "output_config" not in client.calls[0], (
        "Call 1 must NOT carry `output_config` — that would constrain "
        "the reasoning step and erase the 10-17% reasoning-quality benefit."
    )
    # Call 2: output_config.format.type == "json_schema" pointing at a schema.
    assert "output_config" in client.calls[1], (
        "Call 2 MUST carry `output_config` so the SDK enforces the plan "
        "schema via constrained decoding."
    )
    oc = client.calls[1]["output_config"]
    assert isinstance(oc, dict)
    fmt = oc.get("format")
    assert isinstance(fmt, dict) and fmt.get("type") == "json_schema", (
        f"Call 2 output_config.format.type must be 'json_schema'; got {fmt!r}"
    )
    # The schema fragment for `step='plan'` must be the plan schema (not implplan).
    from bin._planner import schemas
    assert fmt.get("schema") is schemas.PLAN_SCHEMA_V1, (
        "Call 2 output_config.format.schema is not PLAN_SCHEMA_V1 — schema "
        "fragment binding may have drifted from §D.1"
    )

    assert isinstance(result.call2_emitted_json, dict)


def test_invoke_planner_implplan_step_uses_implplan_schema(tmp_slug_dir, monkeypatch):
    """IJ.5d: step='implplan' wires the implplan schema (not plan) into Call 2's output_config."""
    from bin._planner import two_call, schemas

    client = _MockClient()
    inputs = two_call.PlannerInputs(
        recon_findings="recon", qa_findings="qa", research_findings="research",
        lessons_findings="lessons", repo_state_summary="main",
        prior_plan_json='{"plan": "fake"}',
        tier="Tier 1",
    )
    two_call.invoke_planner(
        slug="acceptance_ij5d", step="implplan", inputs=inputs,
        client=client,
    )
    assert len(client.calls) == 2
    fmt = client.calls[1]["output_config"]["format"]
    assert fmt["schema"] is schemas.IMPLPLAN_SCHEMA_V1, (
        "step='implplan' should bind IMPLPLAN_SCHEMA_V1 in Call 2's output_config"
    )
