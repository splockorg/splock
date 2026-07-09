"""Closed-enum exit codes for `bin/render_plan` and `bin/verify_plan`.

Per implplan §B.impl.4 (lines 1144-1156) — codes 0-7, 11 are §B-local.
Codes 5 and 7 are also entries in the cross-CLI shared exit-code registry
(implplan §A.impl.3a). Codes 16 (`verify_plan_rejected`) and 9
(`sealed_path_refused`) are reserved by chain-orchestrated CLIs and are
NOT emitted from this binary — they are emitted by the chain driver (§A)
when interpreting our exit codes.

See also `schemas/README.md` for the sealed-state inventory entries that
§G.impl will register (this build does not author hooks, only documents
the references §G consumes).
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_PLAN_NOT_FOUND = 2
EXIT_JSON_MALFORMED = 3
EXIT_SCHEMA_REJECTED = 4
EXIT_UNSUPPORTED_SCHEMA_VERSION = 5
EXIT_TEMPLATE_ERROR = 6
EXIT_ATOMIC_WRITE_FAILED = 7
EXIT_DRIFT = 11

# real_tests_at_junctions SC2 (T3): the orchestrator's tests_enabled
# contract was violated (prose entry, or a phantom selector whose path
# appears in no task's file_paths_touched). DISTINCT from
# EXIT_SCHEMA_REJECTED so the chain driver does not collapse it into the
# generic 16 `verify_plan_rejected` family — the operator signal is "the
# plan emission is structurally defective at the tests_enabled level; fix
# the plan authoring", not "schema parse problem". Mirrored verbatim in
# `bin/_chain_overnight/exit_codes.py` (the cross-CLI shared registry)
# with its own verdict mapping in `state_machine.py`. Allocated 44 as the
# lowest slot free across the A.impl.3a registry (39 is §J
# `failure_capture_idempotent_noop`; 43 is §D `amend_post_apply_invalid`).
EXIT_TESTS_ENABLED_REJECTED = 44

# Sealed-state path inventory extension (per §B.impl.10; §G.impl reads this
# list when authoring the `chain-sealed-state-delete-block` PreToolUse hook):
#   docs/plans/<slug>/<slug>_plan.json
#   docs/plans/<slug>/<slug>_orchestrator.json
# Derived MD files are NOT sealed — operator anchor-block edits must survive.

ALL_CODES = frozenset(
    {
        EXIT_OK,
        EXIT_USAGE,
        EXIT_PLAN_NOT_FOUND,
        EXIT_JSON_MALFORMED,
        EXIT_SCHEMA_REJECTED,
        EXIT_UNSUPPORTED_SCHEMA_VERSION,
        EXIT_TEMPLATE_ERROR,
        EXIT_ATOMIC_WRITE_FAILED,
        EXIT_DRIFT,
        EXIT_TESTS_ENABLED_REJECTED,
    }
)
