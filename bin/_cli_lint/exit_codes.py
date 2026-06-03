"""Closed-enum exit codes for `bin/cli-lint`.

Per implplan §A.impl.3a (cross-CLI shared exit-code registry) and
§N.impl.9 #2 RATIFIED 2026-05-21: single registry slot for
`cli_lint_violation` (code 38); the specific rule is discriminated in
structured stderr `{"error":"cli_lint_violation","rule":"REQ_<X>_..."}`.
"""

from __future__ import annotations

EXIT_OK: int = 0
"""All standing requirements pass on all checked CLIs."""

EXIT_USAGE: int = 1
"""Argparse / CLI-usage error."""

EXIT_DRIVER_CRASH: int = 2
"""Driver itself crashed: catalog missing, malformed table, etc."""

EXIT_CLI_LINT_VIOLATION: int = 38
"""One or more standing requirements failed. Rule discriminated in
structured stderr. §N.impl.9 #2 RATIFIED 2026-05-21."""


CLI_LINT_EMITTED_CODES = frozenset(
    {
        EXIT_OK,
        EXIT_USAGE,
        EXIT_DRIVER_CRASH,
        EXIT_CLI_LINT_VIOLATION,
    }
)
"""Aggregate set for caller-side membership checks."""


# Codes that are valid for ANY CLI in the catalog (universal floor).
# Per A.impl.3a "shared exit-code registry": every chain-orchestrated
# CLI is allowed to emit any of these without per-CLI documentation.
CHAIN_REGISTRY_CODES: frozenset[int] = frozenset(
    {
        0,    # OK (universal)
        1,    # usage (universal)
        2,    # driver crash (universal)
        3,    # transition refused / schema invalid (varied per CLI)
        4,    # schema rejected / no entries
        5,    # insufficient budget / generic input rejection
        6,    # marker trigger vague (K-scoped)
        7,    # atomic write failed (shared)
        8,    # ralph NO
        9,    # ralph NEEDS_HUMAN / sealed_path_refused (A)
        10,   # phase_boundary_halt / divergence-found
        11,   # wall_clock_cap / lazy_dump_cap
        12,   # cost_cap
        13,   # token_cap
        14,   # guardrail
        15,   # orphan_detected
        16,   # verify_plan_rejected
        17,   # retry_exceeded
        18,   # operator_killed / task_outside_develop_plan
        19,   # E-scoped reserved
        20,   # chain_refused
        21,   # chain_foreign_sentinel
        22,   # H-scoped
        23,   # H-scoped
        24,   # H-scoped
        25,   # L route_issue
        26,   # L route_issue
        27,   # L route_issue
        28,   # L route_issue
        29,   # E-scoped
        30,   # E-scoped
        31,   # H-scoped
        32,   # J eval gate
        33,   # J eval gate
        34,   # J eval baseline
        35,   # H mark-for-eval / label-score
        36,   # M lessons required field
        37,   # M lessons entry malformed
        38,   # N cli-lint violation
        43,   # D amend_post_apply_invalid (plan_surgical_amend §SC2/§SC6)
    }
)
"""Closed-enum cross-CLI registry per A.impl.3a. cli-lint's REQ_D rule
greps for `sys.exit(<N>)` / `exit <N>` literals + asserts each N is
in this set OR in the per-CLI documented enum (e.g., bin/hook-lint's
codes 10-16 scoped to its own registry).

Code 43 (`amend_post_apply_invalid`) is registered here per
plan_surgical_amend T6e: `bin/_planner/exit_codes.EXIT_AMEND_POST_APPLY_INVALID`
is the surgical-amend post-apply re-validation halt (T2). The amend
dispatch (T6e, `bin/_planner/main.py`) returns the NAMED constant rather
than a bare `sys.exit(43)` literal, so REQ_D does not flag the dispatch
itself; 43 is registered all the same because (a) it IS a live
chain-registry code emitted by the `bin/plan` CLI surface, and (b) the T2
exit-code docstring references the literal `sys.exit(43)` in prose, which
REQ_D's text grep would surface as a false-positive if `bin/plan` ever
joins the catalog table. The numbering rationale (43 = lowest free slot
across the full A.impl.3a registry; NOT 9/39) lives in
`bin/_planner/exit_codes.EXIT_AMEND_POST_APPLY_INVALID`."""


# Per-CLI documented exit-code closed enums (for CLIs that maintain
# their OWN registry distinct from the chain-orchestrated shared one).
# Per A.impl.3a "two-registry split": bin/hook-lint + bin/cli-lint each
# carry their own closed-enum scope.
PER_CLI_DOCUMENTED_ENUMS: dict[str, frozenset[int]] = {
    "bin/hook-lint": frozenset({0, 1, 10, 11, 12, 13, 14, 15, 16}),
    "bin/cli-lint": frozenset({0, 1, 2, 38}),
}
