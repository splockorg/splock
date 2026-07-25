"""Nested-scalar addressing in the surgical-amend apply engine.

Surfaced by the first cross-repo `--amend` run (qum, 2026-07-25). `plan_v1`
carries exactly ONE scalar that is neither a plan-root key nor a keyed-collection
entry — `conceptual_architecture.overview` — and before the allowlist NO op_kind
could address it: the `op_kind` enum is closed and `scalar` was root-only. The
operator's only recourse was to edit it OUT OF BAND and narrate the delta in the
audit row's prose, which defeats the substrate: `ops` stops being a description
of the diff.

The trap this file pins hardest is the FAILURE MODE, not the gap. A patch
addressing `field: "conceptual_architecture.overview"` validates cleanly against
`plan_patch_v1` (the schema types `field` as a plain string) and used to die at
apply-time claiming the field "is not present on the prior plan root" — a message
that describes the symptom and hides the cause, which is what sent the surfacing
run looking in the wrong place. `test_schema_still_admits_the_nested_address`
keeps that schema-level admission explicit so a future in-schema tightening is a
deliberate decision rather than a silent one.
"""

from __future__ import annotations

import copy
import json
import warnings
from pathlib import Path

import jsonschema
import pytest

from bin._planner.patch_apply import (
    NESTED_SCALAR_FIELDS,
    PatchApplyError,
    PatchBoundAdvisory,
    PatchPostApplyInvalid,
    apply_patch,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
NESTED = "conceptual_architecture.overview"


def _patch_schema() -> dict:
    return json.loads((REPO_ROOT / "schemas" / "plan_patch_v1.schema.json").read_text())


def _plan() -> dict:
    """A minimal plan valid against `plan_v1` (post-apply re-validation runs on
    every successful apply, so an invalid base would mask the behavior here)."""
    return {
        "schema_version": 1,
        "slug": "nested_scalar_probe",
        "phase": "Phase 2",
        "title": "Nested scalar probe",
        "problem_statement": "The overview prose carries stale anchors.",
        "tier": "Tier 2",
        "success_criteria": [
            {"id": "SC1", "criterion": "The overview is amendable in-band."},
            {"id": "SC2", "criterion": "Untouched entries stay byte-identical."},
        ],
        "tasks_skeleton": [
            {"id": "T1", "title": "Address the nested scalar", "depends_on": []},
        ],
        "non_goals": ["A general dotted-path resolver."],
        "conceptual_architecture": {
            "overview": "dataDemand is carried verbatim at :1808-1816.",
            "components": [
                {"name": "engine", "purpose": "Apply keyed ops.", "dependencies": []},
            ],
        },
        "references": [{"kind": "recon", "pointer": "docs/plans/x/x_recon.md"}],
    }


def _op(action: str, field: str, value: str | None = None) -> dict:
    op: dict = {"op_kind": "scalar", "action": action, "address": {"field": field}}
    if value is not None:
        op["value"] = value
    return op


def _patch(*ops: dict) -> dict:
    return {"patch_version": 1, "ops": list(ops)}


# ---------------------------------------------------------------------------
# The schema-level trap.
# ---------------------------------------------------------------------------


def test_schema_still_admits_the_nested_address():
    """`plan_patch_v1` types `address.field` as a plain string, so a dotted path
    passes schema validation and any refusal must come from the apply engine.

    This is deliberate placement, not an oversight: the allowlist lives in
    `patch_apply.NESTED_SCALAR_FIELDS` for the same reason `address.index`'s >= 0
    bound does — the constrained-decoding endpoint's restrictions on this schema.
    Pinned so that moving the check in-schema is a decision someone makes on
    purpose.
    """
    jsonschema.validate(_patch(_op("replace", NESTED, "NEW")), _patch_schema())


def test_op_kind_enum_stays_closed():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            _patch({"op_kind": "nested_scalar", "action": "replace",
                    "address": {"field": NESTED}, "value": "NEW"}),
            _patch_schema(),
        )


# ---------------------------------------------------------------------------
# The capability.
# ---------------------------------------------------------------------------


def test_nested_scalar_replace_applies_and_preserves_everything_else():
    plan = _plan()
    pristine = copy.deepcopy(plan)
    new = "dataDemand is carried verbatim at :1808-1817."

    out = apply_patch(plan, _patch(_op("replace", NESTED, new)))

    assert out["conceptual_architecture"]["overview"] == new
    # Byte/deep-equality preservation (invariant 1): the sibling key inside the
    # same parent object, and every other top-level key, are untouched.
    assert out["conceptual_architecture"]["components"] == pristine[
        "conceptual_architecture"]["components"]
    for key in pristine:
        if key != "conceptual_architecture":
            assert out[key] == pristine[key], key
    # No silent mutation of the caller's input (invariant 3).
    assert plan == pristine


def test_allowlist_is_the_one_plan_v1_nested_scalar():
    """A guard on scope, not on style: if `plan_v1` grows another nested scalar,
    this fails and whoever added it decides whether it belongs in the allowlist."""
    schema = json.loads((REPO_ROOT / "schemas" / "plan_v1.schema.json").read_text())
    nested_scalars = set()
    for parent, spec in schema["properties"].items():
        if spec.get("type") != "object":
            continue
        for child, child_spec in (spec.get("properties") or {}).items():
            if child_spec.get("type") in {"string", "integer", "number", "boolean"}:
                nested_scalars.add(f"{parent}.{child}")
    assert nested_scalars == set(NESTED_SCALAR_FIELDS)


# ---------------------------------------------------------------------------
# The refusals — each names its CAUSE.
# ---------------------------------------------------------------------------


def test_unallowlisted_nested_path_names_the_cause_not_the_symptom():
    with pytest.raises(PatchApplyError) as exc:
        apply_patch(_plan(), _patch(_op("replace", "conceptual_architecture.missing", "X")))
    message = str(exc.value)
    assert "nested paths are addressable only for" in message
    assert NESTED in message
    # The old message blamed the plan root, which is where the surfacing run
    # went looking. It must not come back for a dotted address.
    assert "not present on the prior plan root" not in message


def test_keyed_collection_cannot_be_smuggled_in_by_path():
    """A general dotted resolver would bypass keyed addressing, the reference
    tuple-uniqueness guard, and the op-bounding denominator (T3.A's smuggling
    surface). The allowlist is closed precisely to keep this refused."""
    with pytest.raises(PatchApplyError) as exc:
        apply_patch(_plan(), _patch(_op("replace", "success_criteria.0.criterion", "X")))
    assert "nested paths are addressable only for" in str(exc.value)


@pytest.mark.parametrize("action", ["add", "remove"])
def test_nested_addressing_is_replace_only(action):
    """Every allowlisted nested scalar is `plan_v1`-required and therefore always
    present, so `add` always collides and `remove` can only ever produce a
    post-apply-invalid plan. Refusing both structurally turns a post-round-trip
    exit 43 into an actionable refusal — the same front-running move as the
    per-op-kind VALUE-shape closure — and keeps dotted addressing from implying
    nested key creation/deletion semantics."""
    value = "X" if action == "add" else None
    with pytest.raises(PatchApplyError) as exc:
        apply_patch(_plan(), _patch(_op(action, NESTED, value)))
    message = str(exc.value)
    assert "replace ONLY" in message
    assert "exit 43" in message


def test_missing_parent_object_reports_the_parent():
    plan = _plan()
    del plan["conceptual_architecture"]
    with pytest.raises(PatchApplyError) as exc:
        apply_patch(plan, _patch(_op("replace", NESTED, "X")))
    message = str(exc.value)
    assert "unresolvable" in message
    assert "conceptual_architecture" in message


def test_nested_value_must_be_a_string():
    with pytest.raises(PatchApplyError) as exc:
        apply_patch(
            _plan(),
            _patch({"op_kind": "scalar", "action": "replace",
                    "address": {"field": NESTED}, "value": {"overview": "X"}}),
        )
    assert "requires a string `value`" in str(exc.value)


# ---------------------------------------------------------------------------
# The root path is untouched by the change.
# ---------------------------------------------------------------------------


def test_nested_scalar_is_excluded_from_the_bound_gate():
    """The one safety property this change could silently erode.

    `scalar` ops are excluded from `_enforce_op_bound`'s numerator AND
    denominator, and nested scalars inherit that. The exclusion is correct here
    for two reasons that must BOTH hold for any future allowlist entry: the
    allowlist is closed, so N ops against the one allowlisted path are N
    sequential replaces of the same key rather than fan-out to smuggle through;
    and the overview is prose in the same class as the root scalar
    `problem_statement`, which this gate has always excluded.

    Pinned so that growing the allowlist forces the decision to be re-made
    instead of inherited. A patch of nothing but nested replaces must not trip
    the gate — and must not warn, since a bound advisory is the signal that a
    patch is drifting toward wholesale.
    """
    plan = _plan()
    ops = [_op("replace", NESTED, f"revision {n}") for n in range(12)]
    with warnings.catch_warnings():
        warnings.simplefilter("error", PatchBoundAdvisory)
        out = apply_patch(plan, _patch(*ops))
    assert out["conceptual_architecture"]["overview"] == "revision 11"


def test_schema_version_stays_unreplaceable_by_a_scalar_op():
    """No scalar op can replace `schema_version`, and the protection is LAYERED —
    which matters, because either layer alone would look like an accident.

    A non-string value is caught by `_coerce_value_string` (scalar values are
    string-only). A *string* value — `"2"` — sails past the coercer and is caught
    by post-apply `plan_v1` re-validation, since `schema_version` is
    `integer`/`const: 1`. So widening the coercer alone could NOT open a
    migration path; the schema still closes it.

    That is the right outcome — bumping `schema_version` is a version migration,
    not a surgical amend. Both layers are pinned so that removing either one is a
    deliberate act with a failing test attached.
    """
    with pytest.raises(PatchApplyError) as exc:
        apply_patch(
            _plan(),
            _patch({"op_kind": "scalar", "action": "replace",
                    "address": {"field": "schema_version"}, "value": 2}),
        )
    assert "requires a string `value`" in str(exc.value)

    # Second layer: a string value passes the coercer, then fails re-validation.
    with pytest.raises(PatchPostApplyInvalid):
        apply_patch(
            _plan(),
            _patch({"op_kind": "scalar", "action": "replace",
                    "address": {"field": "schema_version"}, "value": "2"}),
        )


def test_root_scalar_replace_still_works():
    out = apply_patch(_plan(), _patch(_op("replace", "title", "Retitled")))
    assert out["title"] == "Retitled"


def test_root_scalar_miss_still_blames_the_root():
    with pytest.raises(PatchApplyError) as exc:
        apply_patch(_plan(), _patch(_op("replace", "no_such_field", "X")))
    assert "not present on the prior plan root" in str(exc.value)
