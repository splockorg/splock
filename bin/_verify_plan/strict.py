"""Strict-mode invariants beyond JSON Schema validation.

Per implplan §B.impl.9 line 1343:
    "--strict mode adds invariants beyond schema: task-id uniqueness within
     a document; depends_on references resolve to defined task IDs;
     plan_ref in orchestrator resolves to an existing <slug>_plan.json"

Per real_tests_at_junctions SC2 (T3), the orchestrator branch also
enforces the tests_enabled entry contract (the deterministic twin of the
SC1 prompt nudge in `bin/_planner/prompt_templates.TESTS_ENABLED_CONTRACT`):
every `tasks[].tests_enabled` entry must be a runnable pytest selector
bound to the plan's `file_paths_touched`, or absent (`[]`). The
`TYPED_GATE_COMMAND_PREFIX` is RESERVED, do-not-author per the T6/SC3
narrow decision — and per the operator-approved post-T8 follow-up patch
an AUTHORED entry is now a contract violation (the junction-time
recognition in `bin/_retry_loop/sdk_spawners.py` is deliberately
unchanged). A task with `tests_enabled: []` MAY declare WHY via the
`VERIFICATION_KIND_MARKER_PREFIX` exemption marker in its `test_plan[]`
(the narrowed SC3 shape — see
`docs/plans/_closed/real_tests_at_junctions/typed_gate_command_decision.md`).
Violations raise the DISTINCT `TestsEnabledContractError` (mapped to its
own plan-defect exit code, NOT the generic schema-rejection 4 → chain 16
collapse).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from bin._render_plan.json_loader import (
    SchemaRejectedError,
    load_plan_json,
    validate_against_schema,
)

PlanKind = Literal["plan", "orchestrator"]


TYPED_GATE_COMMAND_PREFIX = "gate_cmd:"
"""Reserved prefix marking a tests_enabled entry as an explicitly-typed
non-pytest gate command (the Aider `--test-cmd` model, research F2.1).
A per-entry `{kind: gate_cmd, command}` tagged-union is not
schema-representable (STD.6 Anthropic-endpoint oneOf/if-then rejection),
so the string-prefix convention is the enforceable shape.

This constant is the SINGLE SOURCE for the prefix; consumers
(`bin/_retry_loop/sdk_spawners.py`) import it rather than re-declaring.

T6 (SC3) DECISION — NARROWED (2026-06-10). The evidence base
(T*_verification.json in exactly two slugs ever; the one closed slug,
`_closed/verifier_sdk_wiring`, used plain pytest selectors throughout)
does NOT support generalizing a typed-command runner. The prefix is
therefore RESERVED, do-not-author: per the operator-approved post-T8
follow-up patch, `_check_tests_enabled_contract` REJECTS an authored
prefixed entry (the original recognition-only allowance was prompt-only
and let an authored entry count vacuously as advance-ok junction
credit). The junction-time classifier in
`bin/_retry_loop/sdk_spawners.py` still recognizes the prefix —
deliberately unchanged defense-in-depth — and NO gate verdict path
executes it. Tasks with no pytest-expressible acceptance use
`tests_enabled: []` plus the `VERIFICATION_KIND_MARKER_PREFIX`
exemption marker instead; authoring `gate_cmd:` becomes legal only on
the GENERALIZE flip. Full rationale:
`docs/plans/_closed/real_tests_at_junctions/typed_gate_command_decision.md` §4."""


VERIFICATION_KIND_MARKER_PREFIX = "verification_kind:"
"""Reserved `test_plan[].test_id` prefix declaring the narrowed SC3
exemption (real_tests_at_junctions T6): a task whose acceptance genuinely
cannot be a pytest selector (substrate/heredoc artifacts, sealed-path
operator-applied bundles, doc-review deliverables) ships
`tests_enabled: []` and declares WHY via a test_plan entry whose
`test_id` starts with this prefix — e.g.::

    {"test_id": "verification_kind: artifact_review",
     "asserts": "what the non-pytest verification establishes",
     "fixture": "how it is performed (doc-review / staged bundle / ...)"}

The declared kind is the stripped remainder after the prefix (free
vocabulary; common kinds: ``artifact_review``, ``doc_review``,
``operator_applied``). There is NO command runner behind the marker —
that is the point of the narrow branch.

Why test_plan-hosted: `schemas/orchestrator_v1.schema.json` task objects
carry ``additionalProperties: false``, so a task-level
``verification_kind`` field is not schema-representable without a schema
change; ``test_plan[].test_id`` is a free-form required string, making
the prefix convention the deterministic, schema-valid shape.

Validator semantics (`_check_tests_enabled_contract`):

* the marker is OPTIONAL — a bare `tests_enabled: []` bookkeeping task
  without it still passes (T3-no-false-positive-empty unchanged);
* a marker with an EMPTY kind is malformed → contract violation;
* a marker on a task that ALSO carries tests_enabled entries is
  contradictory (exempt + graded cannot both hold) → contract violation;
* a test_id that is NOT an exact marker but matches
  `_VERIFICATION_KIND_NEAR_MISS_RE` is a near-miss spelling → contract
  violation with a did-you-mean diagnostic (post-T8 follow-up lint);
* TWO OR MORE exact-prefix markers on one task → contract violation
  (the old first-wins resolution was silent).

Junction semantics (`bin/_retry_loop/sdk_spawners.junction_collect_check`):
exempt tasks are surfaced in the verdict's ``exempt_tasks`` list but do
NOT alter advance semantics — an all-exempt covering set still refuses
with ``empty_union`` (a test_gate over zero runnable tests is vacuous;
use a ``review_gate`` junction instead). Decision record:
`docs/plans/_closed/real_tests_at_junctions/typed_gate_command_decision.md`."""


# Near-miss recognition for the exemption marker (post-T8 follow-up
# lint): case-insensitive 'verification' + optional separator run of
# space/underscore/hyphen + 'kind' + optional 's' + optional whitespace
# + optional ':'. Catches e.g. 'verification-kind:', 'Verification_kind:',
# ' verification_kind:' (leading space), 'verification_kind :' (space
# before colon), 'verification_kinds:'. A misspelled marker would
# otherwise silently degrade the task to a plain bookkeeping entry AND
# evade the empty-kind/contradiction coherence checks. Exact well-formed
# markers are short-circuited FIRST (startswith) and never reach this
# regex.
_VERIFICATION_KIND_NEAR_MISS_RE = re.compile(
    r"^\s*verification[ _-]*kinds?\s*:?", re.IGNORECASE
)


def task_verification_exemption(task: dict) -> str | None:
    """Declared verification kind for a task's SC3 exemption marker, or None.

    Scans ``task["test_plan"]`` for the first entry whose ``test_id``
    starts with `VERIFICATION_KIND_MARKER_PREFIX` and carries a
    non-empty kind; returns the stripped kind string. Returns ``None``
    when no well-formed marker is present (malformed markers — empty
    kind — are graded by `_check_tests_enabled_contract`, not here).

    Pure declaration lookup: does NOT check ``tests_enabled`` emptiness;
    the validator owns the coherence rule (marker ⇒ ``tests_enabled``
    must be ``[]``).
    """
    for entry in task.get("test_plan", []) or []:
        if not isinstance(entry, dict):
            continue
        test_id = entry.get("test_id")
        if isinstance(test_id, str) and test_id.startswith(
            VERIFICATION_KIND_MARKER_PREFIX
        ):
            kind = test_id[len(VERIFICATION_KIND_MARKER_PREFIX):].strip()
            if kind:
                return kind
    return None


class TestsEnabledContractError(SchemaRejectedError):
    """tests_enabled contract violations — the DISTINCT plan-defect signal.

    Subclass of `SchemaRejectedError` so legacy callers that catch the
    parent still reject the document; new callers (`bin/verify_plan
    --strict` dispatch, the operator-direct /implplan emission seam in
    `bin/_planner/main.py`) catch this type FIRST and map it to
    `bin/_render_plan/exit_codes.EXIT_TESTS_ENABLED_REJECTED` instead of
    the generic `EXIT_SCHEMA_REJECTED` (which the chain driver collapses
    into exit 16 `verify_plan_rejected`). Per real_tests_at_junctions SC2.
    """

    # Not a pytest test class despite the `Test` prefix (silences
    # PytestCollectionWarning when imported into test modules).
    __test__ = False

    def as_stderr_payload(self) -> dict:
        payload = super().as_stderr_payload()
        payload["error"] = "tests_enabled_contract_rejected"
        return payload


def run_strict_invariants(
    payload: dict, kind: PlanKind, source_path: Path
) -> None:
    """Run cross-field invariants; raise `SchemaRejectedError` on failure.

    The caller (`bin/verify_plan` main) maps the exception to exit code
    4. We reuse the schema-rejection error class because the strict-mode
    failures are semantically "this document is malformed at the
    cross-field level" — the same disposition the chain driver applies
    to schema errors.

    Exception precedence (real_tests_at_junctions SC2): when ANY
    tests_enabled contract violation is present, the raised error is the
    `TestsEnabledContractError` subclass (carrying ALL violations,
    including any generic ones) so the distinct plan-defect signal is
    never masked by a co-occurring generic violation. A document with
    only generic violations raises the plain `SchemaRejectedError`
    exactly as before.
    """
    violations: list[dict] = []
    contract_violations: list[dict] = []

    if kind == "plan":
        violations.extend(_check_task_skeleton_unique_ids(payload))
        violations.extend(_check_skeleton_depends_on_resolves(payload))
    else:
        violations.extend(_check_orch_task_unique_ids(payload))
        violations.extend(_check_orch_depends_on_resolves(payload))
        violations.extend(_check_plan_ref_exists(payload, source_path))
        violations.extend(_check_junctions_after_task_resolves(payload))
        contract_violations = _check_tests_enabled_contract(payload)
        violations.extend(contract_violations)

    if violations:
        if contract_violations:
            raise TestsEnabledContractError(
                path=str(source_path), violations=violations
            )
        raise SchemaRejectedError(
            path=str(source_path), violations=violations
        )


def revalidate_orchestrator_file(path: Path, *, schema: bool = True) -> dict:
    """Re-validate an on-disk `<slug>_orchestrator.json` AFTER a rewrite.

    The SC7 RE-ENTRY seam (real_tests_at_junctions T7): every mutation
    path that rewrites the sealed orchestrator must re-run the SC2
    validator on the bytes that actually landed, so prose can never be
    re-introduced into `tests_enabled` post-emit. Two rewrite paths bind
    to this seam:

    * **`/implplan --reopen` regeneration** — `bin/_planner/main.py`
      calls this post-write (in addition to its pre-write T3 seam, which
      rejects BEFORE the bytes land). The post-write call validates the
      ON-DISK artifact itself, closing the gap between in-memory payload
      validation and what was actually serialized; a contract violation
      rolls the rewrite back (pre-rewrite bytes restored) and exits with
      the distinct plan-defect code 44.
    * **the operator /tmp re-serializer CONVENTION** — the orchestrator
      is a sealed path (Edit/Write hook-denied), so operators apply
      surgical orchestrator changes via a /tmp script that re-serializes
      the JSON through Python I/O. This is a convention, not a fixed code
      path (the yaml_refactor T8 hand-patch traversed it ~2026-06-09 with
      ZERO validation), so the binding is contractual: any re-serializer
      MUST call this helper immediately after its write — or shell out to
      the equivalent ``python -m bin._render_plan.verify <path> --kind
      orchestrator --strict`` — and abort/restore the prior bytes on a
      raise (treat ANY exception from this function as "the rewrite is
      rejected; do not leave it on disk").

    Validation order (deliberate): parse (`load_plan_json`) → SC2 strict
    invariants (`run_strict_invariants`, kind="orchestrator") → optional
    JSON Schema (`validate_against_schema`, default ON). Strict runs
    FIRST so the distinct `TestsEnabledContractError` signal is never
    masked by a co-occurring generic schema violation — the same
    precedence rule `run_strict_invariants` applies internally to its own
    violation classes.

    Raises:
        TestsEnabledContractError: prose / phantom-selector re-introduced
            (the distinct plan-defect signal; exit-44 family).
        SchemaRejectedError: generic strict-invariant or JSON Schema
            violations.
        JsonMalformedError: the rewrite did not leave parseable JSON.
        PlanNotFoundError: no file at ``path``.

    Returns the validated payload dict on success, so callers can keep
    operating on it without a second read.
    """
    path = Path(path)
    payload = load_plan_json(path)
    run_strict_invariants(payload, "orchestrator", path)
    if schema:
        validate_against_schema(payload, "orchestrator", source_path=str(path))
    return payload


def _check_task_skeleton_unique_ids(payload: dict) -> list[dict]:
    seen: set[str] = set()
    dupes: list[str] = []
    for task in payload.get("tasks_skeleton", []) or []:
        tid = task.get("id")
        if tid in seen:
            dupes.append(tid)
        else:
            seen.add(tid)
    if not dupes:
        return []
    return [
        {
            "path": "/tasks_skeleton",
            "message": f"duplicate task ids in tasks_skeleton: {dupes}",
            "validator": "strict-unique-ids",
        }
    ]


def _check_skeleton_depends_on_resolves(payload: dict) -> list[dict]:
    ids: set[str] = {
        task["id"]
        for task in payload.get("tasks_skeleton", []) or []
        if "id" in task
    }
    unresolved: list[tuple[str, str]] = []
    for task in payload.get("tasks_skeleton", []) or []:
        for dep in task.get("depends_on", []) or []:
            if dep not in ids:
                unresolved.append((task.get("id", "<unknown>"), dep))
    if not unresolved:
        return []
    return [
        {
            "path": f"/tasks_skeleton[{task_id}]/depends_on",
            "message": f"depends_on '{dep}' does not resolve to a defined task id",
            "validator": "strict-depends-on-resolution",
        }
        for task_id, dep in unresolved
    ]


def _check_orch_task_unique_ids(payload: dict) -> list[dict]:
    seen: set[str] = set()
    dupes: list[str] = []
    for task in payload.get("tasks", []) or []:
        tid = task.get("id")
        if tid in seen:
            dupes.append(tid)
        else:
            seen.add(tid)
    if not dupes:
        return []
    return [
        {
            "path": "/tasks",
            "message": f"duplicate task ids in tasks: {dupes}",
            "validator": "strict-unique-ids",
        }
    ]


def _check_orch_depends_on_resolves(payload: dict) -> list[dict]:
    ids: set[str] = {
        task["id"] for task in payload.get("tasks", []) or [] if "id" in task
    }
    unresolved: list[tuple[str, str]] = []
    for task in payload.get("tasks", []) or []:
        for dep in task.get("depends_on", []) or []:
            if dep not in ids:
                unresolved.append((task.get("id", "<unknown>"), dep))
    if not unresolved:
        return []
    return [
        {
            "path": f"/tasks[{task_id}]/depends_on",
            "message": (
                f"depends_on '{dep}' does not resolve to a defined task id"
            ),
            "validator": "strict-depends-on-resolution",
        }
        for task_id, dep in unresolved
    ]


def _check_plan_ref_exists(payload: dict, source_path: Path) -> list[dict]:
    plan_ref = payload.get("plan_ref")
    if not plan_ref:
        return []
    # plan_ref is relative to the same directory as the orchestrator JSON.
    candidate = source_path.parent / plan_ref
    if candidate.exists():
        return []
    return [
        {
            "path": "/plan_ref",
            "message": (
                f"plan_ref '{plan_ref}' does not resolve to an existing file "
                f"(checked {candidate})"
            ),
            "validator": "strict-plan-ref-exists",
        }
    ]


def _check_junctions_after_task_resolves(payload: dict) -> list[dict]:
    task_ids: set[str] = {
        task["id"] for task in payload.get("tasks", []) or [] if "id" in task
    }
    unresolved: list[tuple[str, str]] = []
    for junction in payload.get("junctions", []) or []:
        after = junction.get("after_task")
        if after and after not in task_ids:
            unresolved.append((junction.get("id", "<unknown>"), after))
    if not unresolved:
        return []
    return [
        {
            "path": f"/junctions[{j_id}]/after_task",
            "message": (
                f"after_task '{after}' does not resolve to a defined task id"
            ),
            "validator": "strict-junction-resolution",
        }
        for j_id, after in unresolved
    ]


# --------------------------------------------------------------------------- #
# tests_enabled contract (real_tests_at_junctions SC2 / T3)
# --------------------------------------------------------------------------- #


def _normalize_repo_path(path: str) -> str:
    """Normalize a repo-relative path for membership comparison."""
    return path[2:] if path.startswith("./") else path


def _selector_path_component(entry: str) -> str:
    """Path component of a pytest selector (everything before `::`)."""
    return entry.split("::", 1)[0]


def _is_pytest_selector_shaped(entry: str) -> bool:
    """Cheap shape check mirroring `is_runnable_pytest_selector` semantics.

    Whitespace is checked on the PATH COMPONENT only (per
    `bin/_retry_loop/sdk_spawners.py` — parametrized node-IDs may carry
    whitespace inside `[...]` and are valid). The path must end `.py`.
    """
    path = _selector_path_component(entry)
    if not path or not path.endswith(".py"):
        return False
    if any(ch.isspace() for ch in path):
        return False
    return True


def _check_tests_enabled_contract(payload: dict) -> list[dict]:
    """Classify each task's tests_enabled entries; return contract violations.

    Per real_tests_at_junctions SC2, each entry must be ONE of:

    * a pytest selector (`path/to/test.py` or `path/to/test.py::node`)
      whose path component appears in ANY task's `file_paths_touched`
      across the plan (path-membership, NOT `is_file()` — selector-to-be
      files do not exist until their task runs). Note the SC1 prompt
      contract nudges the stricter SAME-task binding at authoring time;
      the deterministic plan-level rule here is the plan's SC2 wording
      (any task).
    * nothing (`tests_enabled: []` is legitimate for bookkeeping/doc
      tasks and must not false-positive). Such a task MAY declare the
      narrowed SC3 exemption via a `VERIFICATION_KIND_MARKER_PREFIX`
      test_plan marker (see that constant's docstring); when the marker
      is present it must be well-formed (non-empty kind) and the task's
      tests_enabled must actually be empty — a marker co-occurring with
      tests_enabled entries is contradictory and rejected.

    A `TYPED_GATE_COMMAND_PREFIX` entry is a violation: the prefix is
    RESERVED do-not-author under the T6/SC3 NARROW decision, and per the
    operator-approved post-T8 follow-up patch the do-not-author guard is
    a validator error, not a prompt nudge (the original allowance let an
    authored entry pass without file binding and count vacuously as
    advance-ok junction credit). Junction-time recognition in
    `bin/_retry_loop/sdk_spawners.py` is deliberately unchanged.

    Anything else IS the defect (no NLP needed: an entry that does not
    parse as a selector is prose). Diagnostics name the offending task
    id and the entry verbatim.

    Operates purely on the in-memory payload dict; the canonical on-disk
    source for per-task tests_enabled is `<slug>_orchestrator.json` per
    SC5/T2 (`bin/_retry_loop/briefing.CANONICAL_TESTS_ENABLED_SOURCE` —
    callers that start from a file path resolve through those helpers).
    """
    tasks = payload.get("tasks", []) or []

    all_touched_paths: set[str] = set()
    for task in tasks:
        for fp in task.get("file_paths_touched", []) or []:
            if isinstance(fp, str):
                all_touched_paths.add(_normalize_repo_path(fp))

    violations: list[dict] = []
    for task in tasks:
        tid = task.get("id", "<unknown>")
        violations.extend(_check_verification_kind_markers(task, tid))
        for idx, entry in enumerate(task.get("tests_enabled", []) or []):
            pointer = f"/tasks[{tid}]/tests_enabled[{idx}]"
            if not isinstance(entry, str) or not entry.strip():
                violations.append(
                    {
                        "path": pointer,
                        "message": (
                            f"task {tid} tests_enabled[{idx}] is not a "
                            f"string selector: {entry!r}"
                        ),
                        "validator": "strict-tests-enabled-contract",
                    }
                )
                continue
            if entry.startswith(TYPED_GATE_COMMAND_PREFIX):
                violations.append(
                    {
                        "path": pointer,
                        "message": (
                            f"task {tid} tests_enabled[{idx}] authors a "
                            f"typed gate command: \"{entry}\" — the "
                            f"'{TYPED_GATE_COMMAND_PREFIX}' prefix is "
                            f"RESERVED (recognition-only) under the "
                            f"narrowed SC3 branch and must NOT be "
                            f"authored; declare a non-pytest acceptance "
                            f"via tests_enabled: [] plus a "
                            f"'{VERIFICATION_KIND_MARKER_PREFIX} <kind>' "
                            f"test_plan marker instead. Authoring becomes "
                            f"legal only on the GENERALIZE flip — see "
                            f"docs/plans/_closed/real_tests_at_junctions/"
                            f"typed_gate_command_decision.md §4"
                        ),
                        "validator": "strict-tests-enabled-contract",
                    }
                )
                continue
            if not _is_pytest_selector_shaped(entry):
                violations.append(
                    {
                        "path": pointer,
                        "message": (
                            f"task {tid} tests_enabled[{idx}] is not a "
                            f"runnable pytest selector (path/to/test.py or "
                            f"path/to/test.py::node): "
                            f"\"{entry}\" — design prose belongs in that "
                            f"task's test_plan[] entries, never in "
                            f"tests_enabled (real_tests_at_junctions SC2)"
                        ),
                        "validator": "strict-tests-enabled-contract",
                    }
                )
                continue
            path = _normalize_repo_path(_selector_path_component(entry))
            if path not in all_touched_paths:
                violations.append(
                    {
                        "path": pointer,
                        "message": (
                            f"task {tid} tests_enabled[{idx}] selector "
                            f"\"{entry}\" is a phantom selector: its path "
                            f"component '{path}' appears in NO task's "
                            f"file_paths_touched across the plan "
                            f"(path-membership check, not is_file() — the "
                            f"file need not exist on disk yet, but some "
                            f"task must commit to authoring it)"
                        ),
                        "validator": "strict-tests-enabled-contract",
                    }
                )
    return violations


def _check_verification_kind_markers(task: dict, tid: str) -> list[dict]:
    """Coherence checks for the narrowed SC3 exemption marker (T6).

    The marker itself is optional. When present:

    * an empty declared kind (``"verification_kind:"`` with nothing
      after) is malformed;
    * co-occurrence with a non-empty ``tests_enabled`` is contradictory
      (a task cannot be both exempt-from-pytest and pytest-graded);
    * two or more exact-prefix markers on one task are rejected (the
      old first-wins resolution was silent);
    * a test_id that is NOT an exact marker but matches
      `_VERIFICATION_KIND_NEAR_MISS_RE` is rejected with a did-you-mean
      diagnostic (post-T8 follow-up lint — a misspelled marker would
      otherwise silently degrade to a plain bookkeeping entry and evade
      every check above).
    """
    violations: list[dict] = []
    marker_indices: list[int] = []
    for idx, entry in enumerate(task.get("test_plan", []) or []):
        if not isinstance(entry, dict):
            continue
        test_id = entry.get("test_id")
        if not isinstance(test_id, str):
            continue
        if not test_id.startswith(VERIFICATION_KIND_MARKER_PREFIX):
            if _VERIFICATION_KIND_NEAR_MISS_RE.match(test_id):
                violations.append(
                    {
                        "path": f"/tasks[{tid}]/test_plan[{idx}]/test_id",
                        "message": (
                            f"task {tid} test_plan[{idx}] test_id "
                            f"\"{test_id}\" is a near-miss spelling of the "
                            f"verification_kind exemption marker — did you "
                            f"mean '{VERIFICATION_KIND_MARKER_PREFIX} "
                            f"<kind>' (exact prefix, e.g. "
                            f"'{VERIFICATION_KIND_MARKER_PREFIX} "
                            f"artifact_review')? A misspelled marker would "
                            f"silently degrade the task to a plain "
                            f"bookkeeping entry and evade the SC3 "
                            f"coherence checks (real_tests_at_junctions "
                            f"SC3, narrowed branch)"
                        ),
                        "validator": "strict-tests-enabled-contract",
                    }
                )
            continue
        marker_indices.append(idx)
        kind = test_id[len(VERIFICATION_KIND_MARKER_PREFIX):].strip()
        if not kind:
            violations.append(
                {
                    "path": f"/tasks[{tid}]/test_plan[{idx}]/test_id",
                    "message": (
                        f"task {tid} test_plan[{idx}] declares a malformed "
                        f"verification_kind exemption marker (empty kind): "
                        f"\"{test_id}\" — the marker must carry a non-empty "
                        f"kind after '{VERIFICATION_KIND_MARKER_PREFIX}', "
                        f"e.g. '{VERIFICATION_KIND_MARKER_PREFIX} "
                        f"artifact_review' (real_tests_at_junctions SC3, "
                        f"narrowed branch)"
                    ),
                    "validator": "strict-tests-enabled-contract",
                }
            )
    if len(marker_indices) > 1:
        violations.append(
            {
                "path": f"/tasks[{tid}]/test_plan",
                "message": (
                    f"task {tid} declares {len(marker_indices)} "
                    f"verification_kind exemption markers (test_plan "
                    f"index(es) {marker_indices}) — at most ONE marker is "
                    f"allowed per task; duplicate markers previously "
                    f"resolved first-wins silently "
                    f"(real_tests_at_junctions SC3, narrowed branch)"
                ),
                "validator": "strict-tests-enabled-contract",
            }
        )
    if marker_indices and (task.get("tests_enabled") or []):
        violations.append(
            {
                "path": f"/tasks[{tid}]/tests_enabled",
                "message": (
                    f"task {tid} declares a verification_kind exemption "
                    f"marker (test_plan index(es) {marker_indices}) but ALSO "
                    f"carries tests_enabled entries — contradictory: the "
                    f"exemption means 'no pytest-expressible acceptance', so "
                    f"tests_enabled must be [] when the marker is present "
                    f"(real_tests_at_junctions SC3, narrowed branch)"
                ),
                "validator": "strict-tests-enabled-contract",
            }
        )
    return violations


__all__ = [
    "TYPED_GATE_COMMAND_PREFIX",
    "VERIFICATION_KIND_MARKER_PREFIX",
    "TestsEnabledContractError",
    "revalidate_orchestrator_file",
    "run_strict_invariants",
    "task_verification_exemption",
]
