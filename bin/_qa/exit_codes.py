"""Closed-enum exit codes for `bin/qa`.

Per implplan §A.impl.3a shared exit-code registry. Mirrors
`bin._planner.exit_codes` so a chain-driver caller examining `$?` can
disambiguate halt families uniformly across the planner and qa CLI
surfaces.

- 0  = success
- 1  = usage error (slug dir missing, <slug>_recon.md missing)
- 7  = atomic_write_failed (§B-shared)
- 8  = target_exists_no_reopen. RETAINED in the closed enum for cross-CLI
       parity with §D's planner code 8
       (`bin._planner.exit_codes.EXIT_TARGET_EXISTS_NO_REOPEN`), but qa NO
       LONGER raises it: re-runs against an existing <slug>_qa.md now append
       by default (or branch to <slug>_qa_<N>.md with --new-file, or
       overwrite with --reopen), so there is nothing to refuse. The constant
       stays so chain-driver $? interpretation remains uniform across CLIs.
- 17 = SDK call failed (qa's analogue of §D's EXIT_SDK_RETRY_EXHAUSTED).
       Distinct numeric so callers can tell a qa SDK failure from a
       planner Structured-Outputs retry exhaustion without parsing the
       error envelope. Single-call SDK error has no "retry" semantics —
       this code means the qa MD response was empty / API-errored.

Code 17 is new to the closed enum; if the chain-driver wires qa into
its pre-plan phase later, register it in §A.impl.3a alongside 16.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_ATOMIC_WRITE_FAILED = 7

EXIT_TARGET_EXISTS_NO_REOPEN = 8
"""Target artifact (`<slug>_qa.md`) already exists and `--reopen` was
NOT passed. Operator must pass `--reopen` to overwrite the existing
target intentionally.

Added per std_command_operator_extensions TB. Distinct numeric from
EXIT_USAGE (1) so chain-driver callers can disambiguate a deliberate
overwrite-refusal from a generic parse/usage failure without inspecting
the stderr envelope. Numeric value 8 matches
`bin._planner.exit_codes.EXIT_TARGET_EXISTS_NO_REOPEN` for cross-CLI
consistency. Unlike the planner side, qa has NO cascade dependency
(qa.md has no downstream artifact whose existence would also need to
block a --reopen), so the surrounding logic is simpler — `--reopen`
unconditionally bypasses the gate."""

EXIT_SDK_CALL_FAILED = 17

ALL_CODES = frozenset(
    {
        EXIT_OK,
        EXIT_USAGE,
        EXIT_ATOMIC_WRITE_FAILED,
        EXIT_TARGET_EXISTS_NO_REOPEN,
        EXIT_SDK_CALL_FAILED,
    }
)
