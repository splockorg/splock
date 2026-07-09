"""J.7 — Every `bin/*/exit_codes.py` constant documented in implplan §A.impl.3a.

Per inventory:
- Source: §10 §4.2 finding #11 (§P exit codes 40/41/42 split into
  bin/_intent/exit_codes.py — by-design per A.impl.3a scope-ownership).
- Expected outcome: every `EXIT_*` constant across `bin/_*/exit_codes.py`
  is named in the implplan §A.impl.3a master registry; no orphan codes.
"""

from __future__ import annotations

import pytest
import re
from pathlib import Path


pytestmark = pytest.mark.acceptance


EXIT_CONSTANT_RE = re.compile(r"^(EXIT_[A-Z0-9_]+)\s*=\s*([0-9]+)", re.MULTILINE)


def _collect_exit_codes(repo_root: Path) -> dict[str, list[tuple[str, int]]]:
    """Walk bin/_*/exit_codes.py and collect {module: [(name, code), ...]}."""
    out: dict[str, list[tuple[str, int]]] = {}
    for path in (repo_root / "bin").glob("_*/exit_codes.py"):
        text = path.read_text(encoding="utf-8")
        codes = [(m.group(1), int(m.group(2))) for m in EXIT_CONSTANT_RE.finditer(text)]
        module = str(path.parent.relative_to(repo_root))
        out[module] = codes
    return out


# Constants exempt from the master-registry documentation requirement.
# Universal codes (EXIT_OK / EXIT_USAGE) live in every module's exit_codes.py
# but the registry only documents non-trivial entries.
#
# Per-constant exemptions for modules NOT in the chain-orchestrated CLI
# scope (per implplan §A.impl.3a "Scope of the shared registry") are
# documented inline with the rationale:
#
#  * `EXIT_SDK_CALL_FAILED` (bin/_qa) — `bin/qa` is invoked interactively
#    by operators and through the QA subagent's planner toolchain, not by
#    the chain driver. The §A.impl.3a registry is scoped to chain-
#    orchestrated CLIs whose `$?` the chain driver consumes; qa's exit
#    codes flow only to the operator's shell and the subagent transcript.
#    Per `bin/_qa/exit_codes.py`'s module docstring: "if the chain-driver
#    wires qa into its pre-plan phase later, register it in §A.impl.3a
#    alongside 16."
EXEMPT_FROM_REGISTRY = frozenset({
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_SDK_CALL_FAILED",
    # CCOR.1 codes (R-exit-codes). EXIT_NOT_PAUSED (22) and
    # EXIT_ALREADY_PAUSED (23) are emitted by `bin/_chain_pause/main.py` +
    # `bin/_chain_resume/main.py` and live in `bin/_chain_overnight/
    # exit_codes.py` as the cross-CLI shared registry. The splock
    # implplan §A.impl.3a is the pre-CCOR.1 master registry; CCOR.1 owns
    # its own master-registry section in `docs/plans/_closed/ccor_1/implplan.md`
    # + `design_resolutions.md::R-exit-codes`. Per T-10's userguide work,
    # the operator-facing exit-code table consolidates both code numbers.
    # The splock-acceptance master-registry check is scoped to
    # pre-CCOR.1 codes; exempting the CCOR.1 codes here preserves the
    # acceptance test's original audit posture without dragging CCOR.1
    # vocabulary into the prior plan's documentation.
    "EXIT_NOT_PAUSED",
    "EXIT_ALREADY_PAUSED",
})


# Modules whose ENTIRE exit_codes.py is exempt from the §A.impl.3a master-
# registry walk because the binary is NOT in the chain-orchestrated CLI
# scope. Per implplan §A.impl.3a "Scope of the shared registry": the
# registry exists for the chain driver's `$?` disambiguation surface;
# binaries invoked only by operators / sub-agents / out-of-chain skills
# manage their own local closed-enum namespace.
#
#  * `bin/_orchestrator_query` (code_next_ready_pick T1/T2) — backs
#    `bin/orchestrator-next-ready`, an out-of-chain read-only picker
#    invoked exclusively by the `/code` skill (one-token-slug path).
#    The picker's exit codes flow to the skill's bash dispatch table
#    in `commands/code.md`; they are never observed by
#    bin/chain-overnight. Codes are scope-disambiguated from the
#    registry by calling binary per the documented F2.2 pattern. The
#    M-acceptance test
#    (`test_acceptance_M_orchestrator_query_exit_codes.py`) enforces
#    the picker's local-namespace contract.
EXEMPT_MODULES_FROM_REGISTRY = frozenset({
    "bin/_orchestrator_query",
})


def _constant_to_family_name(name: str) -> str:
    """Convert EXIT_VERIFY_PLAN_REJECTED → verify_plan_rejected.

    The implplan §A.impl.3a registry uses lowercase `family` names (e.g.,
    `verify_plan_rejected`), not the uppercase EXIT_* constants. The test
    matches on the family name form.
    """
    return name[len("EXIT_"):].lower() if name.startswith("EXIT_") else name.lower()


def test_every_exit_constant_in_master_registry(repo_root):
    """J.7a: every EXIT_* constant's family name appears in implplan §A.impl.3a."""
    implplan = (repo_root / "docs" / "plans" / "splock"
                / "splock_implplan.md").read_text(encoding="utf-8")

    codes = _collect_exit_codes(repo_root)
    assert codes, "No bin/_*/exit_codes.py files found — unexpected"

    orphans: list[tuple[str, str]] = []
    for module, code_list in codes.items():
        if module in EXEMPT_MODULES_FROM_REGISTRY:
            continue
        for name, _ in code_list:
            if name in EXEMPT_FROM_REGISTRY:
                continue
            family = _constant_to_family_name(name)
            # Either uppercase constant name OR lowercase family name must appear.
            if name not in implplan and family not in implplan:
                orphans.append((module, name))

    assert not orphans, (
        "EXIT_* family names not documented in implplan §A.impl.3a master registry:\n"
        + "\n".join(f"  {m}: {n} (family: {_constant_to_family_name(n)})"
                    for m, n in orphans)
    )


# Documented intentional collisions per Pass 5 Finding 1 resolution (Option c).
# Each entry's set lists the EXIT_* names that legitimately share the numeric
# code across modules. Adding a new entry here requires updating implplan
# §A.impl.3a to document the multi-name semantics + the operator rationale.
INTENTIONAL_COLLISIONS: dict[int, frozenset[str]] = {
    # Code 16 — plan-rejection consolidation. The planner exits 16 on
    # `error_max_structured_output_retries`; the retry-loop + chain driver
    # propagate the same code as "verify_plan_rejected" (a superset of plan-
    # rejection-class causes including JSON malformed / schema rejected /
    # template error). Userguide §13.3 documents 16 = SDK retry exhausted
    # (the most common cause). All names map to "code 16 = a downstream
    # subprocess refused on plan-shape grounds."
    16: frozenset({"EXIT_SDK_RETRY_EXHAUSTED", "EXIT_VERIFY_PLAN_REJECTED"}),

    # Code 8 — precondition-violation refusal family. The planner uses 8
    # for EXIT_TARGET_EXISTS_NO_REOPEN (re-running `/plan` or `/implplan`
    # when the output already exists; refuses + tells operator to delete
    # the prior artifact first per `bin/_planner/main.py:343`). The
    # update_orchestrator CLI uses 8 for EXIT_TASK_OUTSIDE_DEVELOP_PLAN_AUTHORITY
    # (`deferred` / `cancelled` task touched by `--from-develop-plan`;
    # refuses + structured stderr per §E.impl.5). Code 8 was free at the
    # 2026-05-22 update_orchestrator renumber (the renumber moved
    # `task_outside_develop_plan_authority` 18 → 8 to resolve operator-
    # facing ambiguity with `EXIT_OPERATOR_KILLED = 18`). Both share
    # "refused because of a precondition violation that the operator
    # must resolve before re-running" semantics; scope-disambiguated by
    # calling CLI. The splock §A.impl.3a documents 8 =
    # `task_outside_develop_plan_authority`; planner's
    # `target_exists_no_reopen` joins the family here.
    8: frozenset({"EXIT_TARGET_EXISTS_NO_REOPEN",
                  "EXIT_TASK_OUTSIDE_DEVELOP_PLAN_AUTHORITY"}),

    # Code 22 — parallel "thing-not-found" semantics. morning-review owns 22
    # as EXIT_QUEUE_ENTRY_NOT_FOUND (userguide §13.3); regression-replay
    # uses 22 for EXIT_CASE_NOT_FOUND. Both have the same operator handling
    # (the named entity doesn't exist in the per-slug store).
    22: frozenset({"EXIT_QUEUE_ENTRY_NOT_FOUND", "EXIT_CASE_NOT_FOUND"}),

    # Code 2 — usage / argument / not-found errors at argparse-class. Each
    # module uses 2 as its "you asked for something that doesn't exist or
    # is malformed" code. Not operator-facing in userguide §13.3.
    # EXIT_USAGE (orchestrator_query) joins the family per the picker's
    # closed-enum docstring (`bin/_orchestrator_query/exit_codes.py`): the
    # picker uses 2 as the argparse-class usage code (matching the universal
    # convention) while the chain-orchestrated CLIs use 2 for their module-
    # scoped "not-found / argparse-class" semantics — same operator handling.
    2: frozenset({"EXIT_DRIVER_CRASH", "EXIT_ENUM_VIOLATION",
                  "EXIT_ORIGIN_LINE_NOT_FOUND", "EXIT_PLAN_NOT_FOUND",
                  "EXIT_USAGE"}),

    # Code 5 — schema-validation family. Schema-validation modules use 5
    # for UNSUPPORTED_SCHEMA / UNSUPPORTED_SCHEMA_VERSION. When a chain
    # exits 5, the operator reads userguide §13.3 + inspects the trailing
    # logs to disambiguate.
    5: frozenset({"EXIT_UNSUPPORTED_SCHEMA",
                  "EXIT_UNSUPPORTED_SCHEMA_VERSION"}),

    # Code 10 — chain-phase-boundary vs out-of-chain picker-slug-missing.
    # Chain driver uses 10 for PHASE_BOUNDARY_HALT (operator-facing per
    # userguide §13.3); the orchestrator_query picker uses 10 for
    # SLUG_NOT_FOUND. Different binaries, scope-disambiguated by calling
    # CLI per the F2.2 pattern documented in
    # `bin/_orchestrator_query/exit_codes.py`. The picker is out-of-chain
    # (invoked only by the `/code` skill) so a chain-driver `$?=10` is
    # never ambiguous in operator practice.
    10: frozenset({"EXIT_PHASE_BOUNDARY_HALT", "EXIT_SLUG_NOT_FOUND"}),

    # Code 11 — module-scoped "drift / cap / json-missing" codes. Chain
    # driver uses 11 for WALL_CLOCK_CAP (operator-facing); state-divergence
    # uses 11 for DRIFT (operator-facing); orchestrator_query picker uses
    # 11 for ORCHESTRATOR_JSON_MISSING (out-of-chain). Three different
    # modules, three semantics; operator context (which CLI was running)
    # disambiguates.
    11: frozenset({"EXIT_DRIFT", "EXIT_WALL_CLOCK_CAP",
                   "EXIT_ORCHESTRATOR_JSON_MISSING"}),

    # Code 12 — chain-cost-cap vs out-of-chain picker-json-malformed.
    # Chain driver uses 12 for COST_CAP_EXCEEDED (operator-facing per
    # userguide §13.3); orchestrator_query picker uses 12 for
    # ORCHESTRATOR_JSON_MALFORMED. Scope-disambiguated by calling CLI;
    # the picker is out-of-chain.
    12: frozenset({"EXIT_COST_CAP_EXCEEDED",
                   "EXIT_ORCHESTRATOR_JSON_MALFORMED"}),

    # Code 17 — SDK-class failure family. Retry-loop uses 17 for
    # RETRY_EXCEEDED (the test-step retry cap exhausted in §F.impl.3 per
    # userguide §13.3); qa CLI uses 17 for SDK_CALL_FAILED (a single-call
    # SDK error has no "retry" semantics, but the operator-facing
    # interpretation — "an SDK call failed past the binary's retry/no-
    # retry budget" — is the same family). Per the `bin/_qa/exit_codes.py`
    # module docstring: "if the chain-driver wires qa into its pre-plan
    # phase later, register it in §A.impl.3a alongside 16."
    17: frozenset({"EXIT_RETRY_EXCEEDED", "EXIT_SDK_CALL_FAILED"}),

    # Code 20 — chain-refused vs picker-all-blocked. Chain driver uses 20
    # for CHAIN_REFUSED (sentinel-deny per A.impl.4); orchestrator_query
    # picker uses 20 for NO_READY_TASK_ALL_BLOCKED. Both signal "no
    # forward progress possible" but in different binaries' scopes.
    20: frozenset({"EXIT_CHAIN_REFUSED", "EXIT_NO_READY_TASK_ALL_BLOCKED"}),

    # Code 21 — chain-foreign-sentinel vs picker-all-wip. Chain driver
    # uses 21 for CHAIN_FOREIGN_SENTINEL (mid-chain foreign-sentinel
    # detect per A.impl.4); orchestrator_query picker uses 21 for
    # NO_READY_TASK_ALL_WIP. Scope-disambiguated by calling CLI.
    21: frozenset({"EXIT_CHAIN_FOREIGN_SENTINEL", "EXIT_NO_READY_TASK_ALL_WIP"}),

    # Code 22 — parallel "thing-not-found" / no-ready / not-paused
    # semantics across modules. morning-review owns 22 as
    # EXIT_QUEUE_ENTRY_NOT_FOUND (userguide §13.3); regression-replay uses
    # 22 for EXIT_CASE_NOT_FOUND; orchestrator_query picker uses 22 for
    # NO_READY_TASK_MIXED; CCOR.1 bin/chain-resume uses 22 for
    # EXIT_NOT_PAUSED (per R-exit-codes + R-orphan-detection — semantic
    # parallel: "the expected pause-state entity is not present"). All
    # four signal "the named/expected entity does not exist or is not in
    # the expected state"; scope-disambiguated by calling CLI.
    22: frozenset({"EXIT_QUEUE_ENTRY_NOT_FOUND", "EXIT_CASE_NOT_FOUND",
                   "EXIT_NO_READY_TASK_MIXED", "EXIT_NOT_PAUSED"}),

    # Code 23 — terminal-state-double-close vs picker-plan-complete vs
    # chain-pause-already-held. morning-review uses 23 for
    # TRIAGE_DOUBLE_CLOSE (entry already terminal in mirror per
    # H.impl.3); orchestrator_query picker uses 23 for
    # PLAN_COMPLETE_ALL_DONE; CCOR.1 bin/chain-pause uses 23 for
    # EXIT_ALREADY_PAUSED (per R-exit-codes — semantic parallel: "you
    # tried to enter a state the entity is already in"). All three mean
    # "you tried to operate on an entity that has nothing left to
    # transition to in your requested direction"; scope-disambiguated by
    # CLI.
    23: frozenset({"EXIT_TRIAGE_DOUBLE_CLOSE", "EXIT_PLAN_COMPLETE_ALL_DONE",
                   "EXIT_ALREADY_PAUSED"}),
}


def test_no_unintentional_exit_code_collision_across_modules(repo_root):
    """J.7b: numeric collisions must be in the INTENTIONAL_COLLISIONS allowlist.

    Per Pass 5 Finding 1 resolution + code_next_ready_pick T2 expansion:
    documented collisions are intentional (plan-rejection consolidation,
    parallel not-found semantics, argparse-class family, and out-of-chain
    picker vs chain-orchestrated CLI scope-disambiguation per F2.2). Any
    UNDOCUMENTED collision is a bug.

    Adding to INTENTIONAL_COLLISIONS requires either updating implplan
    §A.impl.3a (for chain-orchestrated CLI codes) to document the multi-
    name semantics + operator rationale, OR — for out-of-chain binaries
    like `bin/orchestrator-next-ready` — citing the binary's own module
    docstring rationale inline above the allowlist entry.
    """
    codes = _collect_exit_codes(repo_root)
    seen: dict[int, set[str]] = {}
    for module, code_list in codes.items():
        for name, value in code_list:
            seen.setdefault(value, set()).add(name)

    unintentional: dict[int, set[str]] = {}
    for v, names in seen.items():
        if len(names) <= 1:
            continue
        allowed = INTENTIONAL_COLLISIONS.get(v, frozenset())
        if names != allowed:
            extras = names - allowed
            if extras:
                unintentional[v] = names

    assert not unintentional, (
        "Numeric exit-code collisions NOT in INTENTIONAL_COLLISIONS allowlist:\n"
        + "\n".join(
            f"  code {v}: {sorted(names)} (allowed: {sorted(INTENTIONAL_COLLISIONS.get(v, set()))})"
            for v, names in unintentional.items()
        )
        + "\n\nResolve by:\n"
        + "  (1) renumber the offending name to a free code, OR\n"
        + "  (2) document the collision in INTENTIONAL_COLLISIONS with operator rationale."
    )
