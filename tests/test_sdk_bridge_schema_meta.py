"""Constrained emission must survive schemas that declare the 2020-12 draft.

The live ``claude`` CLI validates its structured-output schema with a
validator that has no 2020-12 meta-schema registered::

    Error: --json-schema is not a valid JSON Schema: no schema with key
    or ref "https://json-schema.org/draft/2020-12/schema"

Every schema this repo ships declares exactly that draft — so every live
constrained emission (plan/implplan Call 2, the reviewer rubric binding)
failed with an opaque ``ProcessError: exit code 1`` while the whole test
suite stayed green: mocks don't validate schemas, and the inline fragments
tests pass around carry no ``$schema`` key. Found by the first full live
``bin/plan`` run against an adopter repo (qum, 2026-07-10).

The fix is ``bin._sdk_bridge.strip_schema_meta_keys``, applied at BOTH SDK
transport boundaries (the SubscriptionClient options builder and the
retry-loop reviewer spawner) so shipped schema files stay fully-declared
for the in-repo ``jsonschema`` validators.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bin._env_paths import plugin_root
from bin._sdk_bridge import SubscriptionClient, strip_schema_meta_keys

REPO_ROOT = plugin_root()


# --------------------------------------------------------------------------- #
# The strip itself                                                              #
# --------------------------------------------------------------------------- #


def test_meta_keys_are_stripped_and_constraints_survive() -> None:
    fmt = {
        "type": "json_schema",
        "schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "plan_v1.schema.json",
            "type": "object",
            "required": ["slug"],
            "properties": {"slug": {"type": "string"}},
        },
    }
    out = strip_schema_meta_keys(fmt)
    assert "$schema" not in out["schema"] and "$id" not in out["schema"]
    assert out["schema"]["required"] == ["slug"]
    assert out["type"] == "json_schema"
    # The input is not mutated — callers may reuse the shipped constant.
    assert "$schema" in fmt["schema"]


@pytest.mark.parametrize("passthrough", [None, "not-a-dict", {"type": "json_schema"}])
def test_non_schema_shapes_pass_through_untouched(passthrough) -> None:
    assert strip_schema_meta_keys(passthrough) is passthrough


def test_a_schema_without_meta_keys_is_returned_as_is() -> None:
    fmt = {"type": "json_schema", "schema": {"type": "object"}}
    assert strip_schema_meta_keys(fmt) is fmt


# --------------------------------------------------------------------------- #
# The strip is load-bearing: the shipped schemas really do declare 2020-12.    #
# --------------------------------------------------------------------------- #


def test_every_shipped_schema_declares_the_draft_the_cli_rejects() -> None:
    """If this ever fails, the strip may have become dead code — re-check
    against a live CLI before removing it."""
    shipped = sorted((REPO_ROOT / "schemas").glob("*.schema.json"))
    assert shipped, "no shipped schemas found"
    declaring = [
        p.name
        for p in shipped
        if json.loads(p.read_text(encoding="utf-8")).get("$schema", "").endswith(
            "2020-12/schema"
        )
    ]
    assert declaring, "no shipped schema declares 2020-12 anymore"


def test_the_reviewer_rubric_constant_declares_it_too() -> None:
    from bin._retry_loop.rubric import TEST_STEP_RUBRIC_SCHEMA_V1

    assert TEST_STEP_RUBRIC_SCHEMA_V1.get("$schema", "").endswith("2020-12/schema")


# --------------------------------------------------------------------------- #
# Both transport boundaries apply it                                            #
# --------------------------------------------------------------------------- #


class _CapturingOptions:
    """Stands in for ClaudeAgentOptions; records what the bridge builds."""

    last_kwargs: dict | None = None

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs


def test_the_subscription_client_strips_before_building_options(monkeypatch) -> None:
    client = SubscriptionClient()
    monkeypatch.setattr(client, "_options_cls", lambda: _CapturingOptions)
    _CapturingOptions.last_kwargs = None

    built = client.messages._build_options(
        model="claude-opus-4-8",
        system=None,
        output_format={
            "type": "json_schema",
            "schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
            },
        },
    )
    assert built is not None
    sent = _CapturingOptions.last_kwargs["output_format"]
    assert "$schema" not in sent["schema"]


def test_the_reviewer_spawner_routes_through_the_strip() -> None:
    """Wiring guard: the spawner builds its options inline, so pin the call
    site textually — a strip nothing calls strips nothing."""
    src = (REPO_ROOT / "bin" / "_retry_loop" / "sdk_spawners.py").read_text(
        encoding="utf-8"
    )
    assert "strip_schema_meta_keys({" in src, (
        "sdk_spawners no longer routes its output_format through "
        "strip_schema_meta_keys; the live CLI will reject the rubric schema"
    )
