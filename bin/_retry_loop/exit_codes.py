"""Closed-enum exit codes for `bin/_retry_loop/main.py`.

Per splock implplan §A.impl.3a (cross-CLI shared exit-code
registry, implplan lines 452-528). §F's slot in the shared registry:

| Code | Family | Origin |
|---|---|---|
| 5 | `unsupported_schema_version` | shared with §B / §D — rubric version refusal per §F.impl.5 forward-compat |
| 7 | `atomic_write_failed` | shared with §B — morning-review entry append failure |
| 10 | `phase_boundary_halt` | §F-owned for HALT verdicts per §F.impl.8 |
| 17 | `retry_exceeded` | §F-owned for test-step + boundary cap exhaustion per §F.impl.3 |
| 16 | `verify_plan_rejected` | shared — when the rubric schema is rejected at decode time |



The retry-loop CLI does NOT propagate child-binary exit codes verbatim
beyond these registry slots; downstream errors raised inside the loop
are wrapped to 10 (`phase_boundary_halt`) so the chain driver's
disambiguator (`bin/_chain_overnight/exit_codes.py`) can map them.

Notes
-----
- **Code 10 ownership.** Both §F and §E claim code 10 in the shared
  registry. They are scope-disambiguated by binary: this module's code
  10 is the RUNTIME §F.9 HALT verdict; §E's code 10 reuses the same
  number for chain-context `done → wip` refusal per §E.impl.5.
  Callers reading `$?` MUST know which binary they invoked.

- **Code 17 emission paths.** Three call sites:
  1. Test-step iteration counter exhaustion (`iteration_loop.run_loop`
     returns `IterationResult.HALT_CAP_EXHAUSTED`).
  2. Phase-boundary unified-counter exhaustion (§F.9.4 — emitted by
     `phase_boundary_review.run_boundary_review` when no `READY`
     verdict landed before the counter ran out).
  3. R4 == "yes-flagged" tampering detection in any iteration (§F.5
     load-bearing R4 emphasis).

  All three paths emit code 17 with a distinct `halt_reason` string in
  the morning-review entry so the operator can disambiguate.
"""

from __future__ import annotations

# Universal
EXIT_OK = 0
"""Test-step / phase-boundary loop completed without halt."""

EXIT_USAGE = 1
"""argparse usage error; converted from `SystemExit` by `main()`."""

EXIT_DRIVER_CRASH = 2
"""Unexpected exception inside the retry-loop dispatch (not a §F-owned
halt; surfaces as exit 2 so the chain driver can wrap to 10 with the
underlying code preserved in the completion summary)."""

EXIT_UNSUPPORTED_SCHEMA_VERSION = 5
"""Rubric `rubric_version` mismatch per §F.impl.5 forward-compat policy
(`rubric.is_supported_version(...)` returned False). Shared with §B / §D."""

EXIT_ATOMIC_WRITE_FAILED = 7
"""Morning-review entry append failed via §B's `atomic_write` discipline.
Shared with §B per A.impl.3a."""

EXIT_PHASE_BOUNDARY_HALT = 10
"""HALT terminal verdict from the runtime §F.9 reviewer — the prior step
cannot fix the problem without operator judgment (e.g., a scope
conflict). §F-owned in the shared registry per A.impl.3a + §F.impl.8."""

EXIT_VERIFY_PLAN_REJECTED = 16
"""SDK Structured-Output decode failure on the rubric schema. Shared
with §B / §D per A.impl.3a."""

EXIT_RETRY_EXCEEDED = 17
"""Unified retry counter exhausted (any source — Ralph NO,
reviewer_needs_revision, or test_step_retry); OR R4 == "yes-flagged"
tampering. §F-owned in the shared registry per A.impl.3a + §F.impl.3 +
§F.impl.7."""


DRIVER_EMITTED_CODES = frozenset(
    {
        EXIT_OK,
        EXIT_USAGE,
        EXIT_DRIVER_CRASH,
        EXIT_UNSUPPORTED_SCHEMA_VERSION,
        EXIT_ATOMIC_WRITE_FAILED,
        EXIT_PHASE_BOUNDARY_HALT,
        EXIT_VERIFY_PLAN_REJECTED,
        EXIT_RETRY_EXCEEDED,
    }
)


__all__ = [
    "DRIVER_EMITTED_CODES",
    "EXIT_ATOMIC_WRITE_FAILED",
    "EXIT_DRIVER_CRASH",
    "EXIT_OK",
    "EXIT_PHASE_BOUNDARY_HALT",
    "EXIT_RETRY_EXCEEDED",
    "EXIT_UNSUPPORTED_SCHEMA_VERSION",
    "EXIT_USAGE",
    "EXIT_VERIFY_PLAN_REJECTED",
]
