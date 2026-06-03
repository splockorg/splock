"""Per-CLI exemptions for `bin/cli-lint` standing requirements.

Per implplan §N.impl.3 + §N.impl.9 #3 RATIFIED 2026-05-21: typed Python
dict at `bin/_cli_lint/exemptions.py`. Adding an exemption requires
(a) an entry here, (b) a corresponding bracketed annotation in
`docs/cli_tooling_catalog.md` Standing-req compliance column,
(c) a code-comment in the CLI source citing the exemption rationale.
"""

from __future__ import annotations

import enum


class Requirement(enum.Enum):
    """Closed enum of the six standing requirements from plan §N.2."""

    A_ATOMIC_WRITES = "REQ_A_ATOMIC_WRITES"
    B_NO_CROSS_CACHE = "REQ_B_NO_CROSS_CACHE"
    C_HOOK_LOG = "REQ_C_HOOK_LOG"
    D_CLOSED_EXIT_CODES = "REQ_D_CLOSED_EXIT_CODES"
    E_ARGPARSE_STRICT = "REQ_E_ARGPARSE_STRICT"
    F_SOLE_WRITER = "REQ_F_SOLE_WRITER"


# Closed-list exemption table.
#
# Discipline: every exemption here MUST have a matching bracketed
# annotation in docs/cli_tooling_catalog.md (e.g., `C[exempt:self]`).
# The test_catalog_completeness test asserts the two are synchronized.
EXEMPTIONS: dict[str, frozenset[Requirement]] = {
    # `bin/hook-log` cannot self-call without infinite recursion; it IS
    # the structured-log primitive that REQ_C demands every other CLI
    # invoke. Same logic for `bin/log`.
    "bin/hook-log": frozenset({Requirement.C_HOOK_LOG}),
    "bin/log": frozenset({Requirement.C_HOOK_LOG}),
    # `bin/hook-lint` and `bin/cli-lint` are themselves the validators;
    # they log via their own structured-stderr violation surface
    # (analog to bin/hook-log's row schema). Self-call would be
    # nonsensical.
    "bin/hook-lint": frozenset({Requirement.C_HOOK_LOG}),
    "bin/cli-lint": frozenset({Requirement.C_HOOK_LOG}),
    # `bin/security-dispatch.sh` is an umbrella PreToolUse dispatcher;
    # its own args are matcher + tool-name + serialized JSON pass-through
    # to dispatched hooks. Strict-argparse semantics don't apply at the
    # dispatch layer. Per §G.impl.13 + §N.impl.3 REQ_E exemption.
    "bin/security-dispatch.sh": frozenset({Requirement.E_ARGPARSE_STRICT}),
    # The downstream hooks (which it dispatches to) log via bin/hook-log;
    # the dispatcher is observed via downstream rows.
    # No REQ_C exemption needed — security-dispatch.sh does call bin/hook-log
    # for dispatch-error cases per §G.impl.13.

    # ------------------------------------------------------------------
    # LEGACY-NON-COMPLIANT CLIs (v1.4-substrate-ship snapshot).
    #
    # Per §N.impl.9 #4 RATIFIED 2026-05-21: "verify-at-ship + ...
    # 100% pass — OR exempt-list the ones that legitimately fail with
    # documented reasons." The CLIs below were spec'd before §N's
    # standing-requirements catalog crystallized; they pre-date the
    # `allow_abbrev=False` discipline (REQ_E) and the `bin/hook-log`
    # invocation discipline (REQ_C). Each entry below carries an
    # explicit upgrade marker in the catalog's standing-req column;
    # cleanup is tracked as scheduled marker `CLI.1` (allocate at
    # next ship pass).
    #
    # REMOVING any of these exemptions requires the corresponding CLI
    # to ship its REQ_E / REQ_C patch first.
    # ------------------------------------------------------------------

    # REQ_E: argparse `allow_abbrev=False` not yet added — pre-existing
    # CLIs shipped under earlier §X.impl passes. Cleanup deferred to
    # CLI.1 marker.
    "bin/chain-overnight": frozenset({Requirement.E_ARGPARSE_STRICT}),
    "bin/build_briefing": frozenset({Requirement.E_ARGPARSE_STRICT}),
    "bin/eval-baseline": frozenset({Requirement.E_ARGPARSE_STRICT}),
    "bin/eval-gate": frozenset({Requirement.E_ARGPARSE_STRICT}),
    "bin/eval-trend": frozenset({Requirement.E_ARGPARSE_STRICT}),
    "bin/intent": frozenset({Requirement.E_ARGPARSE_STRICT}),
    "bin/marker": frozenset({Requirement.E_ARGPARSE_STRICT}),
    "bin/regression-replay": frozenset({
        Requirement.E_ARGPARSE_STRICT,
        Requirement.C_HOOK_LOG,
    }),
    "bin/render_plan": frozenset({Requirement.E_ARGPARSE_STRICT}),
    "bin/render_spans": frozenset({Requirement.E_ARGPARSE_STRICT}),
    "bin/update_orchestrator": frozenset({Requirement.E_ARGPARSE_STRICT}),
    "bin/verify": frozenset({Requirement.E_ARGPARSE_STRICT}),
    "bin/verify_plan": frozenset({Requirement.E_ARGPARSE_STRICT}),

    # REQ_C: CLIs that don't yet emit via `bin/hook-log` or `bin/log`.
    # Many of these are thin observability tools (render_log, state-
    # divergence-check, git-merge-jsonl, install-merge-drivers,
    # chain-overnight-release-lock) — their primary purpose is
    # producing operator-facing stdout, not auditable side-effects.
    # Cleanup deferred to CLI.1 marker.
    "bin/chain-overnight-release-lock": frozenset({Requirement.C_HOOK_LOG}),
    "bin/git-merge-jsonl": frozenset({Requirement.C_HOOK_LOG}),
    "bin/install-merge-drivers": frozenset({Requirement.C_HOOK_LOG}),
    "bin/render_log": frozenset({Requirement.C_HOOK_LOG}),
    "bin/state-divergence-check": frozenset({Requirement.C_HOOK_LOG}),

    # `bin/chain-status` is a read-only observability stub by design
    # (CCOR.1 T-8 — paused-time accounting + sentinel display). Per the
    # T-8 design_resolutions R-status-display-stub: zero file writes,
    # zero log_row() emissions, zero KNOWN_WRITERS registration. The
    # design is exactly analogous to `bin/render_log` — read+render,
    # stdout-only. The L1 `chain_human_handoff` initiative may add
    # log-emit if that layer shifts to auditable side-effects.
    "bin/chain-status": frozenset({Requirement.C_HOOK_LOG}),

    # `bin/render_status_tree` is a renderer-by-design: it consumes the
    # canonical `<slug>_orchestrator.json` + `_state.json` pair and emits
    # `<slug>_orchestrator_execution_tree.md` as a derived view. Audit
    # lineage comes from the writer chain — `bin/update_orchestrator`
    # invokes the renderer after every state mutation (see
    # `_invoke_render_status_tree` in `bin/_update_orchestrator/main.py`),
    # so every render is causally attributable to a logged state
    # transition. Matches `bin/render_log` / `bin/chain-status` semantics.
    "bin/render_status_tree": frozenset({Requirement.C_HOOK_LOG}),

    # Late-arrivers under the same legacy-CLI.1 cleanup target as the
    # block above — pre-date the post-N.impl.9 strict-argparse +
    # hook-log discipline. Each carries its own catalog row with the
    # matching `[exempt:legacy-CLI.1]` annotation.
    "bin/orchestrator-next-ready": frozenset({
        Requirement.E_ARGPARSE_STRICT,
        Requirement.C_HOOK_LOG,
    }),
    "bin/qa": frozenset({
        Requirement.E_ARGPARSE_STRICT,
        Requirement.C_HOOK_LOG,
    }),
    "bin/sealed-rm": frozenset({Requirement.E_ARGPARSE_STRICT}),
    "bin/wrap": frozenset({
        Requirement.E_ARGPARSE_STRICT,
        Requirement.C_HOOK_LOG,
    }),
}


def is_exempt(cli_name: str, req: Requirement) -> bool:
    """Return True if `cli_name` is exempt from requirement `req`."""
    return req in EXEMPTIONS.get(cli_name, frozenset())
