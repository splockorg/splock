"""Deterministic keyed apply engine for the surgical-amend patch substrate.

Plan `plan_surgical_amend` §SC2, task **T2**. This module is the ENGINE that
takes a loaded `<slug>_plan.json` dict plus a *validated* `plan_patch_v1`
object (a keyed op-list — see `schemas/plan_patch_v1.schema.json`) and returns
the amended plan dict, OR raises loudly. It is a **pure function**: no
`Date.now()`, no randomness, no network, no file I/O. Ops are applied in array
order (the stable, documented determinism contract). The same input pair always
produces the same output.

T2 builds the engine only. Wiring it into the `bin/_planner/main.py` CLI
dispatch is **T6e** — `patch_apply.py` stays independently importable and
unit-testable. The per-op-kind addressing *tightening* (which address key is
required for which `op_kind`), op-count / touched-fraction bounding, the
reference `(kind, pointer)` tuple-uniqueness guard, and `non_goal` index
add-only enforcement are **T3** (added below); T2 implemented the load-bearing
minimum each of its four `tests_enabled` slices pins.

T3 (plan_surgical_amend §SC3) TIGHTENS this same engine — it does NOT rewrite
it — closing addressing per op-kind and adding the op-bounding guard that keeps
amend SURGICAL. Three T3 additions sit on top of the T2 invariants (a fourth,
the symmetric per-op-kind VALUE-shape closure D, was added post-ship for STD.6):

  A. **Op-bounding (anti-smuggle).** A patch that would touch too large a
     fraction of the plan's keyed entries is refused and the operator is
     directed to `--reopen` (wholesale regen is the right tool when a patch
     would touch too much). This defeats the "smuggle a wholesale rewrite via
     replace-on-every-key" attack. The bound counts ALL ops uniformly (an `add`
     is NOT cheaper than a `replace`), and the touched-fraction is computed over
     **post-apply totals** (denominator = keyed-entry count the plan would have
     AFTER the patch applies). It is a PRE-FLIGHT gate: it refuses BEFORE any
     mutation (never a partial apply then refuse). See `_enforce_op_bound`.

  B. **Per-op-kind addressing closure.** T1 left `address` structurally
     permissive (every key optional). T3 enforces, per op-kind, that the
     REQUIRED address key(s) are present + well-typed before the op resolves:
     `id` for success_criterion/task, `name` for component,
     `kind`+`pointer` for reference, `index` (int ≥ 0) for non_goal, `field`
     for scalar. An absent/foreign/ill-typed address key fails loudly with a
     `PatchApplyError` (addressing-class) — distinct from a well-keyed-but-
     not-found miss. See `_require_address`.

  C. **Reference (kind, pointer) tuple-uniqueness.** A `reference` op whose
     `(kind, pointer)` tuple is NON-unique in the prior plan is rejected loudly
     (an ambiguous reference is unaddressable). T0 confirmed the corpus is
     collision-free today; T3 enforces it per-apply. See
     `_assert_reference_tuple_unique`.

  D. **Per-op-kind VALUE-shape closure (STD.6, post-ship).** The symmetric twin
     of B for the *value* side: for a replace/add OBJECT op, the `value` must
     carry that op-kind's `plan_v1`-required fields BEFORE it mutates
     (`component` → name+purpose+dependencies, `task` → id+title+depends_on,
     `success_criterion` → id+criterion, `reference` → kind+pointer). A value
     missing a required field fails loudly with a `PatchApplyError`
     (addressing-class) naming the field — converting what was a post-apply
     `plan_v1` re-validation failure (exit 43, discovered only AFTER the LLM
     round-trip) into a pre-apply exit-1 refusal. Added after the first real
     `--amend` emitted a component value lacking `dependencies`. The required
     sets MIRROR `plan_v1`, so this front-runs exactly the violations
     re-validation would raise; value-CONTENT constraints (enums, minLength)
     stay backstopped by post-apply re-validation. See `_require_value_shape` +
     `_REQUIRED_VALUE_FIELDS`.

Three correctness invariants reviewers check hardest:

1. **Byte / deep-equality preservation (the raison d'être).** Applying a small
   single-collection patch leaves *every* untouched top-level key AND *every*
   untouched keyed-collection entry deep-equal to the original. We mutate ONLY
   the addressed entry on a deep copy of the input; we never round-trip the
   whole document through a reformat/reorder that would rewrite untouched
   regions. This reproduces — and guards against — the **yaml_refactor drift
   class**: a wholesale rewrite that silently reformatted/reordered untouched
   regions. The canonical on-disk form is `json.dumps(..., indent=2,
   sort_keys=True)` (per `bin/_planner/main.py:_write_output`), so untouched
   regions serialize byte-identically as long as their *values* are
   untouched — which surgical mutation guarantees.

2. **Integrity check BEFORE post-apply re-validation (ordering is
   load-bearing).** A `task` *remove* targeting a task id that another task
   references in its `depends_on[]` is refused with `PatchIntegrityError`
   (usage-class) DURING the apply loop — strictly before the whole-plan
   `plan_v1` re-validation runs. No silent removal that leaves a dangling edge.

3. **No silent mutation on failure.** The caller's input dict is deep-copied at
   entry; all mutation happens on the copy. Any precondition failure raises
   before the copy is returned, so the original dict the caller holds is
   unchanged and no partially-applied result is ever surfaced.

Exit-code mapping (owned by `bin/_planner/exit_codes.py`, wired into the CLI in
T6e):

  * `PatchPostApplyInvalid`  → `EXIT_AMEND_POST_APPLY_INVALID` (43 — codes 9 and
                               39 are already taken in the §A.impl.3a registry)
  * `PatchIntegrityError`    → usage-class refusal (a dangling-edge / loud
                               precondition failure; surfaced before post-apply
                               re-validation)
  * `PatchApplyError`        → loud apply-precondition failure (missing-key
                               replace, colliding add, missing-key remove)
"""

from __future__ import annotations

import copy
import warnings
from dataclasses import dataclass, field
from typing import Any

from bin._render_plan.json_loader import SchemaRejectedError, validate_against_schema

__all__ = [
    "PatchApplyError",
    "PatchBoundAdvisory",
    "PatchBoundExceeded",
    "PatchIntegrityError",
    "PatchPostApplyInvalid",
    "OP_BOUND_MIN_TOUCHED",
    "OP_BOUND_REFUSE_FRACTION",
    "OP_BOUND_WARN_FRACTION",
    "OP_KINDS",
    "OP_ACTIONS",
    "TASK_OP_KIND",
    "OpSignature",
    "apply_patch",
    "classify_op",
]


# ---------------------------------------------------------------------------
# Op-classification surface (plan_surgical_amend §SC5 / T5a).
#
# The reconciliation policy (`bin/_planner/reconcile.py`) and the downstream
# Phase-2 tasks (T5b state resync, T5d dispatch) need to classify a patch op by
# its `op_kind` + `action` WITHOUT re-encoding the closed enums the apply engine
# already owns. T5a exposes that classification here (a hook into the existing
# op-kind machinery, per the file_paths_touched contract) so the enums + the
# task-op-kind identity live in ONE place — this module — rather than being
# duplicated as string literals across the reconciler. `classify_op` is a pure,
# loud-on-malformed reader: it does not apply or mutate anything.
# ---------------------------------------------------------------------------

# The closed op-kind set, mirrored from `plan_patch_v1.schema.json` (T1) and the
# routing tables below. Source-of-truth for "is this a known op_kind?" outside
# the apply path.
OP_KINDS: frozenset[str] = frozenset(
    {"success_criterion", "task", "component", "reference", "non_goal", "scalar"}
)

# The closed action set (replace / add / remove).
OP_ACTIONS: frozenset[str] = frozenset({"replace", "add", "remove"})

# The op-kind whose keyed collection IS the task DAG (plan `tasks_skeleton`,
# orchestrator `tasks`). The reconciler keys its DAG policy off this constant.
TASK_OP_KIND: str = "task"


# ---------------------------------------------------------------------------
# Op-bounding thresholds (T3 §SC3) — NAMED, configurable constants with
# DOCUMENTED defaults. These are module-level so the bound is a single tunable
# surface (not magic numbers scattered through the apply path) and so a future
# operator-config layer (settings_registry) could override them without
# touching the engine logic. The test contract pins these documented defaults.
# ---------------------------------------------------------------------------

OP_BOUND_WARN_FRACTION = 0.25
"""Soft-warn threshold (default **> 25%**). When a patch would touch more than
this fraction of the plan's post-apply keyed entries, `apply_patch` emits a
non-fatal `PatchBoundAdvisory` (`warnings.warn`) but still applies the patch.
Advisory-only: it surfaces "this amend is getting broad" without blocking a
legitimately-broad-but-intentional surgical change. Strict `>` comparison: a
patch landing exactly AT 25% does not warn."""

OP_BOUND_REFUSE_FRACTION = 0.50
"""Hard-refuse threshold (default **> 50%**). When a patch would touch more than
this fraction of the plan's post-apply keyed entries, `apply_patch` HARD-REFUSES
with `PatchBoundExceeded` BEFORE mutating anything, and the refusal message
directs the operator to `--reopen` (a patch that rewrites more than half the
plan is a wholesale regen wearing a patch costume — `--reopen` is the correct
tool). Strict `>` comparison: a patch landing exactly AT 50% does not refuse.
This is the load-bearing anti-smuggle cap; the soft-warn above is advisory."""

OP_BOUND_MIN_TOUCHED = 2
"""Minimum touched-keyed-entry count for the fraction gate to engage (default
**2**). A patch touching a SINGLE keyed entry is definitionally surgical — it
cannot be a wholesale-rewrite smuggle regardless of how small the plan is — so
the fraction gate is skipped when `touched < OP_BOUND_MIN_TOUCHED`. Without this
floor, a legitimate single-entry edit on a tiny plan (e.g. removing the sole
success_criterion, which `plan_v1` then catches at post-apply re-validation —
the T2 `post_apply_revalidation` contract) would spuriously hard-refuse at a
100% fraction. The floor keeps the bound a genuine anti-smuggle guard rather
than a tiny-plan tripwire. (The op-COUNT equivalence — add counts the same as
replace — holds independently of this floor: it governs how `touched` is
computed, not whether the gate engages.)"""


# ---------------------------------------------------------------------------
# Exceptions — each maps to a loud failure; none is swallowed.
# ---------------------------------------------------------------------------


@dataclass
class PatchApplyError(Exception):
    """An op's apply precondition failed loudly (no silent mutation).

    Raised for the loud-failure trio: `replace` on a missing key, `add`
    colliding with an existing key, and `remove` on a missing key. The working
    copy is discarded by the caller path (it is never returned), so the input
    plan dict the caller holds is unchanged.
    """

    op_index: int
    op_kind: str
    action: str
    message: str

    def __str__(self) -> str:
        return (
            f"op[{self.op_index}] ({self.op_kind}/{self.action}): {self.message}"
        )


@dataclass
class PatchIntegrityError(Exception):
    """A structural-integrity precondition failed (e.g. dangling depends_on).

    Raised when a `task` remove would leave another task's `depends_on[]`
    pointing at a now-absent id. This fires DURING the apply loop, strictly
    BEFORE the post-apply `plan_v1` re-validation, so an integrity refusal is
    never masked by a downstream schema error. Usage-class, not the post-apply
    exit-9 family.
    """

    op_index: int
    removed_id: str
    referencing_ids: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        refs = ", ".join(self.referencing_ids)
        return (
            f"op[{self.op_index}] (task/remove): removing task id "
            f"{self.removed_id!r} would dangle depends_on edges referenced by "
            f"task(s): {refs}"
        )


@dataclass
class PatchPostApplyInvalid(Exception):
    """The amended plan failed `plan_v1` re-validation after a clean apply.

    Maps to `EXIT_AMEND_POST_APPLY_INVALID` (43). The patch's ops each applied
    without an apply/integrity precondition failure, but the resulting whole
    plan no longer validates against the canonical `plan_v1` schema — the
    engine refuses to surface a schema-broken plan. Carries the underlying
    schema violations for the stderr envelope.
    """

    violations: list[dict] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"amended plan failed plan_v1 re-validation: "
            f"{len(self.violations)} violation(s)"
        )


@dataclass
class PatchBoundExceeded(Exception):
    """The patch would touch too large a fraction of the plan — HARD-REFUSED.

    Raised (BEFORE any mutation) when the touched-keyed-entry fraction exceeds
    `OP_BOUND_REFUSE_FRACTION` (default > 50%). Usage-class refusal (a scope
    refusal, parallel to `PatchIntegrityError` — NOT the post-apply schema
    family). The message directs the operator to `--reopen`: a patch that
    rewrites more than half the plan's keyed entries is a wholesale regen and
    should go through `bin/plan --reopen`, not the surgical-amend path. This is
    the structural defeat of the "smuggle a wholesale rewrite via
    replace-on-every-key" attack.
    """

    touched: int
    total: int
    fraction: float
    refuse_fraction: float = OP_BOUND_REFUSE_FRACTION

    def __str__(self) -> str:
        pct = self.fraction * 100.0
        cap = self.refuse_fraction * 100.0
        return (
            f"amend refused: this patch would touch {self.touched} of "
            f"{self.total} keyed plan entr"
            f"{'y' if self.total == 1 else 'ies'} ({pct:.1f}%), exceeding the "
            f"hard-refuse cap of {cap:.0f}%. A change this broad is a wholesale "
            f"regeneration — re-run with `bin/plan --reopen` instead of "
            f"`--amend` (surgical amend is for narrow, targeted edits)."
        )


class PatchBoundAdvisory(UserWarning):
    """Non-fatal advisory: the patch touches a broad fraction of the plan.

    Emitted via `warnings.warn(...)` when the touched-keyed-entry fraction
    exceeds `OP_BOUND_WARN_FRACTION` (default > 25%) but does NOT exceed the
    hard-refuse cap. Advisory-only — the patch still applies. A `UserWarning`
    subclass so callers/tests can target it precisely with
    `pytest.warns(PatchBoundAdvisory)` and so it is non-fatal under the repo's
    default (non-`error`) warnings filter. The engine stays pure: it raises a
    warning through the stdlib `warnings` machinery rather than writing to
    stdout/stderr directly.
    """


# ---------------------------------------------------------------------------
# Op-kind → keyed-collection routing.
# ---------------------------------------------------------------------------

# Object-valued op kinds address a list of dicts by a stable key.
#   op_kind -> (json_path_to_list, address_key_field, entry_key_field)
# `json_path_to_list` is resolved relative to the plan root; a tuple means a
# nested path (conceptual_architecture.components).
_OBJECT_COLLECTIONS: dict[str, dict[str, Any]] = {
    "success_criterion": {
        "path": ("success_criteria",),
        "address_keys": ("id",),
        "entry_keys": ("id",),
    },
    "task": {
        "path": ("tasks_skeleton",),
        "address_keys": ("id",),
        "entry_keys": ("id",),
    },
    "component": {
        "path": ("conceptual_architecture", "components"),
        "address_keys": ("name",),
        "entry_keys": ("name",),
    },
    "reference": {
        "path": ("references",),
        "address_keys": ("kind", "pointer"),
        "entry_keys": ("kind", "pointer"),
    },
}


def apply_patch(plan: dict, patch: dict) -> dict:
    """Apply a validated `plan_patch_v1` op-list to `plan`; return the amended
    plan, or raise loudly.

    Pure + deterministic: ops are applied in `patch["ops"]` array order. The
    input `plan` is NOT mutated (a deep copy is taken at entry); on any failure
    the copy is discarded and the relevant exception is raised, so no
    partially-applied plan is ever returned.

    Args:
        plan: a loaded `<slug>_plan.json` dict (already a `plan_v1`-shaped
            object). Treated as read-only.
        patch: a `plan_patch_v1`-validated object: `{"patch_version": 1,
            "ops": [...]}`. T2 assumes the patch already passed
            `plan_patch_v1` schema validation upstream (T1's registry); this
            function does NOT re-validate the patch envelope, it consumes it.

    Returns:
        The amended plan dict (a deep copy of `plan` with the ops applied).
        Untouched top-level keys and untouched keyed-collection entries are
        deep-equal to the corresponding regions of `plan`.

    Raises:
        PatchBoundExceeded: the patch would touch more than
            `OP_BOUND_REFUSE_FRACTION` of the plan's post-apply keyed entries
            (T3 anti-smuggle cap); raised PRE-FLIGHT, before any mutation, with
            a message directing the operator to `--reopen`.
        PatchApplyError: a loud apply-precondition failure — a missing-key
            replace, colliding add, missing-key remove, OR (T3) an absent /
            foreign / ill-typed per-op-kind address key, OR (T3) a `reference`
            op addressing a `(kind, pointer)` tuple that is non-unique in the
            prior plan.
        PatchIntegrityError: a `task` remove would dangle another task's
            `depends_on[]` edge (fired before post-apply re-validation).
        PatchPostApplyInvalid: the amended plan failed `plan_v1` re-validation
            (→ exit code 43).

    Warns:
        PatchBoundAdvisory: the patch touches more than `OP_BOUND_WARN_FRACTION`
            (but not more than the refuse cap) of the plan's post-apply keyed
            entries — non-fatal; the patch still applies.
    """
    # Invariant 3: deep-copy first so the caller's dict is never mutated and no
    # partial result can leak out on a mid-loop raise.
    working = copy.deepcopy(plan)

    ops = patch.get("ops") or []

    # T3.A — op-bounding PRE-FLIGHT gate. Compute the touched-keyed-entry
    # fraction over POST-APPLY totals and either warn (advisory) or HARD-REFUSE
    # *before* the apply loop mutates anything. Refusing here (not after the
    # loop) honours the "refuse before mutating — no partial apply then refuse"
    # contract. The bound counts ALL ops uniformly: an `add` is no cheaper than
    # a `replace`, so a many-small-adds patch cannot evade the cap.
    _enforce_op_bound(plan, ops)

    # Determinism: apply in array order (stable + documented). We deliberately
    # do NOT sort or reorder ops — array order is the contract the emitter and
    # the audit log (T6g) agree on.
    for op_index, op in enumerate(ops):
        op_kind = op.get("op_kind")
        action = op.get("action")
        if op_kind in _OBJECT_COLLECTIONS:
            _apply_object_op(working, op_index, op_kind, action, op)
        elif op_kind == "non_goal":
            _apply_non_goal_op(working, op_index, action, op)
        elif op_kind == "scalar":
            _apply_scalar_op(working, op_index, action, op)
        else:  # pragma: no cover - schema bars unknown op_kind upstream
            raise PatchApplyError(
                op_index=op_index,
                op_kind=str(op_kind),
                action=str(action),
                message=(
                    f"unknown op_kind {op_kind!r}; the plan_patch_v1 schema "
                    "should have rejected this upstream"
                ),
            )

    # Invariant 2: post-apply re-validation runs AFTER the per-op apply loop
    # (which is where the dangling-depends_on integrity check fires). So an
    # integrity refusal has already raised by this point; we only reach here if
    # every op applied cleanly. Now confirm the whole plan still validates.
    _revalidate_plan(working)

    return working


# ---------------------------------------------------------------------------
# T5a — op classification (pure reader; no apply, no mutation).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpSignature:
    """The classified `(op_kind, action)` of a single patch op, plus its address.

    The reconciler (and T5b/T5d) consume this instead of poking `op["op_kind"]`
    / `op["action"]` directly, so the closed-enum + task-op-kind knowledge stays
    centralized in this module.

    Attributes
    ----------
    op_index : int
        Position in the patch op-list (array order = the determinism contract).
    op_kind : str
        One of `OP_KINDS`.
    action : str
        One of `OP_ACTIONS`.
    address : dict
        The op's `address` object (copied defensively; empty dict if absent).
    is_task : bool
        Convenience: `op_kind == TASK_OP_KIND`.
    """

    op_index: int
    op_kind: str
    action: str
    address: dict
    is_task: bool


def classify_op(op: dict, op_index: int = 0) -> OpSignature:
    """Classify one patch op into an `OpSignature` (pure; raises on malformed).

    Reads `op_kind` / `action` / `address` and validates the first two against
    the closed enums this module owns. A foreign `op_kind` / `action` raises
    `PatchApplyError` (the same loud-failure exception the apply path uses) so a
    caller cannot silently mis-route an unknown op. Does NOT apply or mutate
    anything — purely a structural read used by the reconciliation policy.

    Args:
        op: a single op object from a `plan_patch_v1` patch's `ops` list.
        op_index: the op's position in the op-list (for the error envelope).

    Returns:
        An `OpSignature`.

    Raises:
        PatchApplyError: `op` is not a dict, or its `op_kind`/`action` is
            outside the closed enums (the schema should have barred this
            upstream, but `classify_op` is the loud last line).
    """
    if not isinstance(op, dict):
        raise PatchApplyError(
            op_index=op_index,
            op_kind="<unknown>",
            action="<unknown>",
            message=f"op must be an object; got {type(op).__name__}",
        )
    op_kind = op.get("op_kind")
    action = op.get("action")
    if op_kind not in OP_KINDS:
        raise PatchApplyError(
            op_index=op_index,
            op_kind=str(op_kind),
            action=str(action),
            message=(
                f"unknown op_kind {op_kind!r}; valid: {sorted(OP_KINDS)}"
            ),
        )
    if action not in OP_ACTIONS:
        raise PatchApplyError(
            op_index=op_index,
            op_kind=str(op_kind),
            action=str(action),
            message=(
                f"unknown action {action!r}; valid: {sorted(OP_ACTIONS)}"
            ),
        )
    addr = op.get("address")
    address = dict(addr) if isinstance(addr, dict) else {}
    return OpSignature(
        op_index=op_index,
        op_kind=op_kind,
        action=action,
        address=address,
        is_task=(op_kind == TASK_OP_KIND),
    )


# ---------------------------------------------------------------------------
# T3.A — op-bounding (anti-smuggle): touched-keyed-entry fraction gate.
# ---------------------------------------------------------------------------

# Every op_kind that addresses a KEYED COLLECTION entry counts toward the bound.
# `scalar` ops target a top-level field, not a keyed-collection entry, so they
# do NOT contribute to the touched-keyed-entries numerator (per SC3: the
# fraction is "touched-keyed-entries / total-keyed-entries across ALL keyed
# collections"). A scalar op is still an op (it could co-occur with keyed ops in
# a multi-op patch), but it neither adds to nor removes from the keyed-entry
# population, so it is invisible to both numerator and denominator.
_KEYED_OP_KINDS: frozenset[str] = frozenset(
    {"success_criterion", "task", "component", "reference", "non_goal"}
)

# Paths (relative to plan root) of every keyed collection counted in the
# denominator. Mirrors `_OBJECT_COLLECTIONS` paths plus the `non_goals` list.
_KEYED_COLLECTION_PATHS: tuple[tuple[str, ...], ...] = (
    ("success_criteria",),
    ("tasks_skeleton",),
    ("conceptual_architecture", "components"),
    ("references",),
    ("non_goals",),
)


def _count_keyed_entries(plan: dict) -> int:
    """Total entries across ALL keyed collections in `plan` (the bound's base
    population). Absent / non-list collections contribute 0."""
    total = 0
    for path in _KEYED_COLLECTION_PATHS:
        node: Any = plan
        for seg in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(seg)
        if isinstance(node, list):
            total += len(node)
    return total


def _enforce_op_bound(plan: dict, ops: list) -> None:
    """Pre-flight op-bounding gate (raises BEFORE any mutation).

    Counts the touched keyed entries and the POST-APPLY keyed-entry total, then:

      * `touched / post_apply_total > OP_BOUND_REFUSE_FRACTION`  → HARD-REFUSE
        (`PatchBoundExceeded`, directing the operator to `--reopen`);
      * `> OP_BOUND_WARN_FRACTION` (but not refuse) → `PatchBoundAdvisory`
        (non-fatal `warnings.warn`).

    Determinism + equivalence contract:

      * **All ops count uniformly.** `touched` = the number of ops addressing a
        keyed collection (add + replace + remove alike — each touches exactly
        one entry). An `add` is NOT cheaper than a `replace`; this is what makes
        "N adds trip the hard-refuse exactly as N replaces would" hold.
      * **Denominator = POST-APPLY total.** The base keyed-entry count, adjusted
        by the net size delta the patch would produce: `+1` per keyed `add`,
        `-1` per keyed `remove` (replaces leave size unchanged). Computed
        analytically from the op-list WITHOUT mutating, so the refuse fires
        before the apply loop. `scalar` ops are excluded from both numerator and
        denominator (they are not keyed-collection entries).
      * **Single-touch floor.** The fraction gate is skipped when
        `touched < OP_BOUND_MIN_TOUCHED` — a single keyed touch is
        definitionally surgical and must not spuriously refuse on a tiny plan
        (see the constant's docstring + the T2 `post_apply_revalidation`
        contract).

    The `plan` passed here is the PRISTINE input (pre-deep-copy is fine — this
    function never mutates).
    """
    touched = 0
    adds = 0
    removes = 0
    for op in ops:
        if op.get("op_kind") not in _KEYED_OP_KINDS:
            continue
        touched += 1
        action = op.get("action")
        if action == "add":
            adds += 1
        elif action == "remove":
            removes += 1

    # Single keyed touch (or none) is always surgical — gate disengaged.
    if touched < OP_BOUND_MIN_TOUCHED:
        return

    base_total = _count_keyed_entries(plan)
    post_apply_total = base_total + adds - removes

    # Guard a degenerate denominator: if the patch would empty every keyed
    # collection, the change is by definition wholesale — refuse. (post-apply
    # total <= 0 only happens when removes meet/exceed the entire population,
    # which `touched >= 2` removes can reach on a tiny plan; that IS a wholesale
    # gut, so directing to --reopen is correct.)
    if post_apply_total <= 0:
        raise PatchBoundExceeded(
            touched=touched,
            total=max(base_total, post_apply_total),
            fraction=1.0,
        )

    fraction = touched / post_apply_total

    if fraction > OP_BOUND_REFUSE_FRACTION:
        raise PatchBoundExceeded(
            touched=touched,
            total=post_apply_total,
            fraction=fraction,
        )

    if fraction > OP_BOUND_WARN_FRACTION:
        warnings.warn(
            (
                f"amend advisory: this patch touches {touched} of "
                f"{post_apply_total} keyed plan entries "
                f"({fraction * 100.0:.1f}%), exceeding the soft-warn threshold "
                f"of {OP_BOUND_WARN_FRACTION * 100.0:.0f}%. The patch will still "
                f"apply, but a broad amend may be better expressed as "
                f"`bin/plan --reopen`."
            ),
            PatchBoundAdvisory,
            stacklevel=2,
        )


# ---------------------------------------------------------------------------
# T3.B — per-op-kind addressing closure: which address key is REQUIRED for
# which op_kind. Enforced before the op resolves so an absent/foreign/ill-typed
# key fails loudly (and distinctly from a well-keyed not-found miss).
# ---------------------------------------------------------------------------

# op_kind -> required address key field(s). Each must be present + a non-empty
# string (non_goal's `index` is special-cased: integer >= 0, not a string).
_REQUIRED_ADDRESS_KEYS: dict[str, tuple[str, ...]] = {
    "success_criterion": ("id",),
    "task": ("id",),
    "component": ("name",),
    "reference": ("kind", "pointer"),
    "scalar": ("field",),
    # non_goal is index-addressed; validated in `_apply_non_goal_op`, not here.
}


# op_kind -> required VALUE-object field(s) for action in {replace, add}, each
# paired with its expected JSON type ("str" = present + non-empty string;
# "list" = present + array, possibly empty). This table MIRRORS the `required`
# arrays of the matching `plan_v1` collection-item subschemas
# (schemas/plan_v1.schema.json):
#   success_criteria[]                   -> ["id", "criterion"]                 (line 48)
#   tasks_skeleton[]                     -> ["id", "title", "depends_on"]       (line 61)
#   conceptual_architecture.components[] -> ["name", "purpose", "dependencies"] (line 91)
#   references[]                         -> ["kind", "pointer"]                 (line 109)
# Keeping this in lock-step with plan_v1 is load-bearing: a replace/add OBJECT
# op whose value omits one of these required fields otherwise sails through the
# apply loop and only fails at the whole-plan post-apply plan_v1 re-validation
# (exit 43) — AFTER the LLM round-trip is spent. `_require_value_shape`
# front-runs that so the refusal is a pre-apply PatchApplyError (exit 1, names
# the missing field). The `test_patch_value_shape` drift-guard asserts this
# table still equals plan_v1's required arrays.
#
# Scope: only the four OBJECT op-kinds are listed. `non_goal` / `scalar` carry
# STRING values (well-formedness owned by `_coerce_value_string`) with no
# multi-field shape to front-run — and plan_v1 permits an empty-string non_goal
# (no minLength on non_goals items), so a "non-empty string" guard there would
# OVER-tighten. Value-CONTENT constraints beyond presence+type (reference.kind's
# closed enum, title/problem_statement minLength, tier's enum) are likewise NOT
# duplicated here; they remain backstopped by the post-apply plan_v1
# re-validation. This check closes only the missing-required-FIELD gap (the
# deterministic exit-43 class the surfacing run hit on a `dependencies`-less
# component replace).
_REQUIRED_VALUE_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "success_criterion": (("id", "str"), ("criterion", "str")),
    "task": (("id", "str"), ("title", "str"), ("depends_on", "list")),
    "component": (("name", "str"), ("purpose", "str"), ("dependencies", "list")),
    "reference": (("kind", "str"), ("pointer", "str")),
}


def _require_address(op_index: int, op_kind: str, action: str, op: dict) -> None:
    """Enforce that the address carries the REQUIRED key(s) for `op_kind`.

    Raises `PatchApplyError` (addressing-class) if a required key is absent or
    not a non-empty string. This closes T1's permissive-`address` surface: a
    `task` op addressed by `name`, or a `scalar` op missing `field`, no longer
    silently resolves to a `(None,)` key — it fails with a clear addressing
    message. Distinct from a well-keyed not-found miss (which the resolve step
    reports separately).
    """
    required = _REQUIRED_ADDRESS_KEYS[op_kind]
    addr = op.get("address") or {}
    for key in required:
        val = addr.get(key)
        if not isinstance(val, str) or not val:
            raise PatchApplyError(
                op_index=op_index,
                op_kind=op_kind,
                action=action,
                message=(
                    f"{op_kind} {action} requires a non-empty string "
                    f"address.{key} (per-op-kind addressing); got {val!r}. "
                    f"The {op_kind} op-kind is addressed by "
                    f"{'+'.join(required)} only."
                ),
            )


def _require_value_shape(op_index: int, op_kind: str, action: str, op: dict) -> None:
    """Enforce that a replace/add OBJECT op's `value` carries the REQUIRED
    `plan_v1` fields for `op_kind`, BEFORE the op mutates anything.

    The symmetric twin of `_require_address`: T3.B closed the per-op-kind
    ADDRESS surface; this closes the per-op-kind VALUE surface that T3 left
    open. Without it, a `replace component` op whose value omits `dependencies`
    applies cleanly and only fails at the whole-plan post-apply `plan_v1`
    re-validation — surfacing as exit 43 AFTER the LLM round-trip is spent,
    instead of a pre-apply, actionable exit-1 refusal that names the missing
    field. Required-field sets MIRROR `plan_v1` (see `_REQUIRED_VALUE_FIELDS`),
    so this front-runs exactly the missing-field violations re-validation would
    raise.

    Only the four object op-kinds (success_criterion / task / component /
    reference) are covered; `non_goal` / `scalar` carry string values handled by
    `_coerce_value_string`. A non-dict `value` is deliberately NOT diagnosed
    here — it falls through to `_coerce_value_object`, which raises the canonical
    "requires an object value" `PatchApplyError` (one message, one owner).

    Raises:
        PatchApplyError: a required value field is absent, or present with the
            wrong type (a `str` field that is non-string / empty, or a `list`
            field that is not an array). Addressing-class sibling -> CLI exit 1.
    """
    required = _REQUIRED_VALUE_FIELDS.get(op_kind)
    if required is None:  # pragma: no cover - only object op-kinds reach here
        return
    value = op.get("value")
    if not isinstance(value, dict):
        # Defer to _coerce_value_object for the canonical non-dict message.
        return
    for field_name, field_type in required:
        present = field_name in value
        val = value.get(field_name)
        if field_type == "str":
            ok = isinstance(val, str) and bool(val)
        else:  # "list" — an array, possibly empty (plan_v1 sets no minItems)
            ok = isinstance(val, list)
        if not ok:
            field_list = ", ".join(name for name, _ in required)
            why = "missing" if not present else "malformed (wrong type / empty)"
            raise PatchApplyError(
                op_index=op_index,
                op_kind=op_kind,
                action=action,
                message=(
                    f"{op_kind} {action} value has a {why} required field "
                    f"{field_name!r}: a {op_kind} value MUST carry {field_list} "
                    f"(per-op-kind value shape, mirrors plan_v1; got "
                    f"{val!r}). Refused pre-apply so the post-apply plan_v1 "
                    f"re-validation (exit 43) is not reached after the "
                    f"round-trip — supply the complete entry value and retry."
                ),
            )


def _assert_reference_tuple_unique(
    working: dict, op_index: int, action: str, target_key: tuple
) -> None:
    """Reject a `reference` op whose (kind, pointer) tuple is NON-unique in the
    prior plan.

    An ambiguous reference tuple is unaddressable — surgically replacing or
    removing "the" reference with that tuple is undefined when two entries share
    it. T0 confirmed the corpus is collision-free today; this enforces it
    per-apply so a future duplicate fails loudly instead of silently mutating an
    arbitrary one of the colliding entries.
    """
    refs = working.get("references")
    if not isinstance(refs, list):
        return
    matches = 0
    for entry in refs:
        if not isinstance(entry, dict):
            continue
        if (entry.get("kind"), entry.get("pointer")) == target_key:
            matches += 1
    if matches > 1:
        raise PatchApplyError(
            op_index=op_index,
            op_kind="reference",
            action=action,
            message=(
                f"ambiguous reference address: (kind, pointer)="
                f"{target_key!r} is NON-unique in the prior plan "
                f"({matches} matching entries). A reference op cannot "
                f"surgically address a duplicate tuple — de-duplicate the "
                f"references via `--reopen` first."
            ),
        )


# ---------------------------------------------------------------------------
# Object-collection ops (success_criterion / task / component / reference).
# ---------------------------------------------------------------------------


def _resolve_collection(working: dict, op_index: int, op_kind: str) -> list:
    """Return the live list for an object op_kind, creating the parent chain
    only for an `add` when it is legitimately absent. For T2 we resolve the
    existing collection; a missing collection surfaces as a not-found on
    replace/remove and as an empty-collection target on add.
    """
    spec = _OBJECT_COLLECTIONS[op_kind]
    path = spec["path"]
    node: Any = working
    for seg in path[:-1]:
        child = node.get(seg)
        if not isinstance(child, dict):
            child = {}
            node[seg] = child
        node = child
    leaf = path[-1]
    coll = node.get(leaf)
    if not isinstance(coll, list):
        coll = []
        node[leaf] = coll
    return coll


def _entry_key(entry: dict, key_fields: tuple[str, ...]) -> tuple:
    return tuple(entry.get(k) for k in key_fields)


def _address_key(op: dict, key_fields: tuple[str, ...]) -> tuple:
    addr = op.get("address") or {}
    return tuple(addr.get(k) for k in key_fields)


def _apply_object_op(
    working: dict, op_index: int, op_kind: str, action: str, op: dict
) -> None:
    # T3.B — per-op-kind addressing closure: the required address key(s) for
    # this op_kind must be present + well-typed BEFORE we resolve. An absent /
    # foreign / ill-typed key fails loudly here (distinct from a well-keyed
    # not-found miss reported by the resolve step below).
    _require_address(op_index, op_kind, action, op)

    # Per-op-kind VALUE-shape closure (the symmetric twin of _require_address):
    # for replace/add, the value MUST carry op_kind's plan_v1-required fields
    # BEFORE we mutate, so a missing field (e.g. a component value without
    # `dependencies`) is a pre-apply exit-1 refusal that names the field rather
    # than a post-apply plan_v1 exit-43 after the round-trip. `remove` carries
    # no value, so it is exempt.
    if action in ("replace", "add"):
        _require_value_shape(op_index, op_kind, action, op)

    spec = _OBJECT_COLLECTIONS[op_kind]
    key_fields = spec["entry_keys"]
    coll = _resolve_collection(working, op_index, op_kind)
    target_key = _address_key(op, key_fields)

    # T3.C — reference (kind, pointer) tuple-uniqueness: an ambiguous reference
    # tuple in the prior plan is unaddressable; reject loudly before resolving.
    if op_kind == "reference":
        _assert_reference_tuple_unique(working, op_index, action, target_key)

    # Find the index of the addressed entry (first match; addressing keys are
    # unique by contract — reference tuple-uniqueness is enforced loudly above).
    found_index = None
    for i, entry in enumerate(coll):
        if isinstance(entry, dict) and _entry_key(entry, key_fields) == target_key:
            found_index = i
            break

    key_repr = _key_repr(key_fields, target_key)

    if action == "replace":
        if found_index is None:
            raise PatchApplyError(
                op_index=op_index,
                op_kind=op_kind,
                action=action,
                message=(
                    f"replace target not found: no {op_kind} entry with "
                    f"{key_repr} exists in the prior plan"
                ),
            )
        # Surgical: replace only this entry's value, leave siblings object-equal.
        coll[found_index] = _coerce_value_object(op, op_index, op_kind, action)

    elif action == "add":
        if found_index is not None:
            raise PatchApplyError(
                op_index=op_index,
                op_kind=op_kind,
                action=action,
                message=(
                    f"add collides with an existing {op_kind} entry: {key_repr} "
                    "is already present in the prior plan"
                ),
            )
        coll.append(_coerce_value_object(op, op_index, op_kind, action))

    elif action == "remove":
        if found_index is None:
            raise PatchApplyError(
                op_index=op_index,
                op_kind=op_kind,
                action=action,
                message=(
                    f"remove target not found: no {op_kind} entry with "
                    f"{key_repr} exists in the prior plan"
                ),
            )
        # Integrity check (Invariant 2): a task remove must not dangle another
        # task's depends_on edge. Fires here, before post-apply re-validation.
        if op_kind == "task":
            removed_id = coll[found_index].get("id")
            _assert_no_dangling_depends_on(working, op_index, removed_id, found_index)
        del coll[found_index]

    else:  # pragma: no cover - schema bars unknown action upstream
        raise PatchApplyError(
            op_index=op_index,
            op_kind=op_kind,
            action=str(action),
            message=f"unknown action {action!r}",
        )


def _assert_no_dangling_depends_on(
    working: dict, op_index: int, removed_id: Any, removed_index: int
) -> None:
    """Refuse a task remove that would leave a dangling depends_on edge.

    Scans every OTHER task's `depends_on[]` (the task being removed is excluded
    by index) for a reference to `removed_id`. Any hit raises
    PatchIntegrityError — loud, before post-apply re-validation.
    """
    tasks = working.get("tasks_skeleton") or []
    referencing: list[str] = []
    for i, task in enumerate(tasks):
        if i == removed_index:
            continue
        if not isinstance(task, dict):
            continue
        deps = task.get("depends_on") or []
        if isinstance(deps, list) and removed_id in deps:
            referencing.append(str(task.get("id")))
    if referencing:
        raise PatchIntegrityError(
            op_index=op_index,
            removed_id=str(removed_id),
            referencing_ids=referencing,
        )


# ---------------------------------------------------------------------------
# non_goal ops (bare string list addressed by index).
# ---------------------------------------------------------------------------


def _apply_non_goal_op(working: dict, op_index: int, action: str, op: dict) -> None:
    # T3.B — non_goal addressing closure. non_goals is a bare STRING list with
    # no natural key, so it is addressed by INTEGER INDEX, never by
    # paraphrase / text match. The contract:
    #   * add    -> appends a new string (index-free; add is the only growth op);
    #   * replace/remove -> index-addressed ONLY (no paraphrase-match remove),
    #     with the index required to be an integer >= 0 and in range; a
    #     stale / out-of-range / negative / non-integer index fails LOUDLY.
    # The `index >= 0` bound is enforced HERE rather than in-schema because the
    # Anthropic structured-output endpoint rejects `minimum` on integer props
    # (see the schema's address.index description).
    non_goals = working.get("non_goals")
    if not isinstance(non_goals, list):
        non_goals = []
        working["non_goals"] = non_goals

    addr = op.get("address") or {}
    index = addr.get("index")

    if action == "add":
        # add appends a new string — index addressing is meaningful only for
        # replace/remove. (A stray address.index on an add is simply ignored;
        # the op_kind/action pair, not the index, decides the semantics.)
        non_goals.append(_coerce_value_string(op, op_index, "non_goal", action))
        return

    # replace / remove are index-addressed; a stale / out-of-range / negative /
    # non-integer index fails loudly (no silent miss, no paraphrase fallback).
    # `bool` is excluded explicitly because `isinstance(True, int)` is True in
    # Python and a boolean index is never a legitimate address.
    if not isinstance(index, int) or isinstance(index, bool):
        raise PatchApplyError(
            op_index=op_index,
            op_kind="non_goal",
            action=action,
            message=(
                f"non_goal {action} requires an integer address.index "
                f"(index-based addressing; no paraphrase match); got {index!r}"
            ),
        )
    if index < 0 or index >= len(non_goals):
        raise PatchApplyError(
            op_index=op_index,
            op_kind="non_goal",
            action=action,
            message=(
                f"non_goal {action} index {index} is out of range "
                f"(non_goals has {len(non_goals)} entr"
                f"{'y' if len(non_goals) == 1 else 'ies'})"
            ),
        )

    if action == "replace":
        non_goals[index] = _coerce_value_string(op, op_index, "non_goal", action)
    elif action == "remove":
        del non_goals[index]
    else:  # pragma: no cover
        raise PatchApplyError(
            op_index=op_index,
            op_kind="non_goal",
            action=str(action),
            message=f"unknown action {action!r}",
        )


# ---------------------------------------------------------------------------
# scalar ops (top-level field addressed by name).
# ---------------------------------------------------------------------------


def _apply_scalar_op(working: dict, op_index: int, action: str, op: dict) -> None:
    # T3.B — scalar addressing closure: a scalar op is addressed by `field`
    # name ONLY (a non-empty string). This is the same contract `_require_address`
    # enforces for the object op-kinds; kept inline here since the scalar path
    # also consumes `field_name` immediately below.
    addr = op.get("address") or {}
    field_name = addr.get("field")
    if not isinstance(field_name, str) or not field_name:
        raise PatchApplyError(
            op_index=op_index,
            op_kind="scalar",
            action=str(action),
            message=(
                "scalar op requires a non-empty string address.field "
                f"(per-op-kind addressing); got {field_name!r}. The scalar "
                "op-kind is addressed by field-name only."
            ),
        )

    if action == "replace":
        if field_name not in working:
            raise PatchApplyError(
                op_index=op_index,
                op_kind="scalar",
                action=action,
                message=(
                    f"replace target not found: scalar field {field_name!r} is "
                    "not present on the prior plan root"
                ),
            )
        working[field_name] = _coerce_value_string(op, op_index, "scalar", action)

    elif action == "add":
        if field_name in working:
            raise PatchApplyError(
                op_index=op_index,
                op_kind="scalar",
                action=action,
                message=(
                    f"add collides with an existing scalar field: {field_name!r} "
                    "is already present on the prior plan root"
                ),
            )
        working[field_name] = _coerce_value_string(op, op_index, "scalar", action)

    elif action == "remove":
        if field_name not in working:
            raise PatchApplyError(
                op_index=op_index,
                op_kind="scalar",
                action=action,
                message=(
                    f"remove target not found: scalar field {field_name!r} is "
                    "not present on the prior plan root"
                ),
            )
        del working[field_name]

    else:  # pragma: no cover
        raise PatchApplyError(
            op_index=op_index,
            op_kind="scalar",
            action=str(action),
            message=f"unknown action {action!r}",
        )


# ---------------------------------------------------------------------------
# Value coercion helpers (deep-copy the value so the patch object stays inert).
# ---------------------------------------------------------------------------


def _coerce_value_object(op: dict, op_index: int, op_kind: str, action: str) -> dict:
    value = op.get("value")
    if not isinstance(value, dict):
        raise PatchApplyError(
            op_index=op_index,
            op_kind=op_kind,
            action=action,
            message=(
                f"{op_kind} {action} requires an object `value`; got "
                f"{type(value).__name__}"
            ),
        )
    # Deep-copy so a later mutation of `working` cannot reach back into the
    # caller's patch object (keeps the function pure w.r.t. its inputs).
    return copy.deepcopy(value)


def _coerce_value_string(op: dict, op_index: int, op_kind: str, action: str) -> str:
    value = op.get("value")
    if not isinstance(value, str):
        raise PatchApplyError(
            op_index=op_index,
            op_kind=op_kind,
            action=action,
            message=(
                f"{op_kind} {action} requires a string `value`; got "
                f"{type(value).__name__}"
            ),
        )
    return value


def _key_repr(key_fields: tuple[str, ...], target_key: tuple) -> str:
    return ", ".join(f"{f}={v!r}" for f, v in zip(key_fields, target_key))


# ---------------------------------------------------------------------------
# Post-apply re-validation (Invariant 2: runs after the apply loop).
# ---------------------------------------------------------------------------


def _revalidate_plan(working: dict) -> None:
    """Re-validate the amended plan against `plan_v1`; raise on failure.

    Reuses §B's `validate_against_schema(..., kind="plan")` so the amended plan
    is held to the exact same `plan_v1` contract a fresh emission would be. A
    violation is re-raised as `PatchPostApplyInvalid` (→ exit code 43) so the
    CLI dispatch (T6e) can map it to a distinct halt family — never persisting a
    schema-broken plan.
    """
    try:
        validate_against_schema(working, "plan", source_path="<amended-plan>")
    except SchemaRejectedError as exc:
        raise PatchPostApplyInvalid(violations=list(exc.violations)) from exc
