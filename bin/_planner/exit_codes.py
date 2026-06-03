"""Closed-enum exit codes for `bin/plan` and `bin/implplan`.

Per implplan §D.impl.3 + §A.impl.3a shared exit-code registry. Codes here
are scoped to the two-call planner invocation surface; codes shared across
the chain-orchestrated CLI surface use the same numeric assignments as
§A.impl.3a so a chain-driver caller examining `$?` can disambiguate halt
families uniformly.

Cross-reference §A.impl.3a (lines 460-510 of splock_implplan.md):
- 0  = success (universal)
- 1  = usage error (POSIX convention)
- 7  = atomic_write_failed (§B)
- 8  = target_exists_no_reopen (§D — operator must pass --reopen to
       overwrite an existing <slug>_plan.json or <slug>_orchestrator.json,
       or pre-delete a downstream artifact to allow a cascade-blocked
       reopen). Added per std_command_operator_extensions TA.
- 16 = verify_plan_rejected family — schema-related halt; §D uses this
       on SDK retry-exhaustion (error_max_structured_output_retries) per
       §D.impl.3 line 2762.
- 43 = amend_post_apply_invalid (plan_surgical_amend §SC2 — a surgical
       --amend patch applied cleanly but the resulting plan no longer
       validates against plan_v1; the engine refuses to persist a
       schema-broken plan). Distinct numeric from 8 (target-exists) and
       16 (SDK retry) so a chain-driver caller can disambiguate the
       amend post-apply-invalid halt family. Added per T2. 43 is the
       lowest slot free across the ENTIRE §A.impl.3a registry (which is
       documented contiguous 0..42 — code 9 is `sealed_path_refused`,
       code 39 is §J `failure_capture_idempotent_noop`). The T0-recorded
       "9 is free" held only for this module's LOCAL `ALL_CODES`
       ({0,1,7,8,16}); the cross-module registry already owned 9. T2
       documents 43 in §A.impl.3a; CLI wiring (`sys.exit(43)`) +
       `CHAIN_REGISTRY_CODES` registration land with the dispatch in T6e.
"""

from __future__ import annotations

# Universal
EXIT_OK = 0
EXIT_USAGE = 1

# §D-owned and shared registry slots
EXIT_ATOMIC_WRITE_FAILED = 7
"""Atomic temp + rename failed during fixture / output write. Shared with
§B per A.impl.3a. The planner module itself does NOT write JSON files
(driver-writes-not-subagent invariant per plan §D.6 criterion 5); this
slot is reserved for the unusual case of CLI-side debug-output writes
where atomic_write is invoked."""

EXIT_TARGET_EXISTS_NO_REOPEN = 8
"""Target artifact (`<slug>_plan.json` or `<slug>_orchestrator.json`)
already exists and `--reopen` was NOT passed. Operator must either:

1. Pass `--reopen` to overwrite the existing target intentionally, OR
2. For cascade-blocked reopens (re-running /plan when the downstream
   `<slug>_orchestrator.json` already exists), delete the downstream
   artifact first — single-step `--reopen` does NOT auto-cascade.

Added per std_command_operator_extensions TA. Distinct numeric from
EXIT_USAGE (1) so chain-driver callers can disambiguate a deliberate
overwrite-refusal from a generic parse/usage failure without inspecting
the stderr envelope."""

EXIT_SDK_RETRY_EXHAUSTED = 16
"""SDK Structured Outputs internal retries exhausted; `ResultMessage.
subtype == "error_max_structured_output_retries"`. Per plan §D.5 +
implplan §D.impl.3 — the driver does NOT retry at its layer; the operator
edits the schema or re-does the recon/research input and re-runs.

Shared with §B's `verify_plan_rejected` family per §A.impl.3a — the
chain driver's caller treats both as schema-related halts."""

EXIT_AMEND_POST_APPLY_INVALID = 43
"""A surgical `--amend` patch applied cleanly against the prior
`<slug>_plan.json`, but the *resulting* plan no longer validates against
`plan_v1` (plan_surgical_amend §SC2 / task T2). `bin/_planner/patch_apply.py`
re-validates the amended plan after applying the keyed op-list and raises
`PatchPostApplyInvalid` (→ this exit code) rather than persisting a
schema-broken plan.

Distinct numeric from `EXIT_TARGET_EXISTS_NO_REOPEN` (8) and
`EXIT_SDK_RETRY_EXHAUSTED` (16): a chain-driver caller examining `$?` must be
able to tell a post-apply-invalid amend halt apart from a deliberate
overwrite-refusal (8) and from a schema-related SDK retry-exhaustion (16),
which are unrelated failure families.

Numbering: 43 (NOT 9, NOT 39). The T0 finding proposed 9 by scanning only
this module's LOCAL `ALL_CODES` ({0,1,7,8,16}); but the planner shares the
§A.impl.3a cross-CLI exit-code namespace, where the DOCUMENTED registry is
contiguous 0..42: code 9 is `sealed_path_refused`
(`bin/_chain_overnight/exit_codes.EXIT_SEALED_PATH_REFUSED`) and code 39 is
§J `failure_capture_idempotent_noop`. (Note the acceptance-J collector only
walks `bin/_*/exit_codes.py`, so it does not see 39/40-42 — relying on its
view is the SAME scoping trap that produced the wrong 9; the authoritative
free-slot source is the §A.impl.3a registry table.) 43 is the lowest slot
free across that full documented registry. Corrected in T2; see the T2
deviation note in `docs/plans/plan_surgical_amend/_t0_findings.md`. The CLI
`sys.exit(43)` wiring + `bin/_cli_lint/exit_codes.CHAIN_REGISTRY_CODES`
registration land with the amend dispatch in T6e.

Ordering note: the dangling-`depends_on` integrity check inside
`patch_apply.py` fires BEFORE this post-apply re-validation, so an
integrity-induced refusal surfaces under its own `PatchIntegrityError`
(a usage-class refusal), not under this code."""

ALL_CODES = frozenset(
    {
        EXIT_OK,
        EXIT_USAGE,
        EXIT_ATOMIC_WRITE_FAILED,
        EXIT_TARGET_EXISTS_NO_REOPEN,
        EXIT_SDK_RETRY_EXHAUSTED,
        EXIT_AMEND_POST_APPLY_INVALID,
    }
)
