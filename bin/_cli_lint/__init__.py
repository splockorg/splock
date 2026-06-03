"""Python entry for `bin/cli-lint` (catalog + standing-requirements static check).

Per implplan §N.impl.2 + §N.impl.3.

Six standing rules (closed enum; rule discriminated in structured stderr):

    REQ_A_ATOMIC_WRITES  — state-writing CLIs use temp+rename atomic discipline
    REQ_B_NO_CROSS_CACHE — no module-level mutable cache in CLI entry points
    REQ_C_HOOK_LOG       — every CLI calls bin/hook-log OR bin/log
    REQ_D_CLOSED_EXITS   — exit code literals live in A.impl.3a registry
                           OR a CLI-documented closed enum
    REQ_E_ARGPARSE_STRICT — argparse uses allow_abbrev=False + no
                           parse_known_args()
    REQ_F_SOLE_WRITER    — each sealed-state path has exactly one
                           bin/ writer (modulo co-writer exemptions)

Single exit code per §N.impl.9 #2 ratification (registry slot 38).
"""

from __future__ import annotations

# Exit-code surface (matches the cross-cutting registry §A.impl.3a).
EXIT_OK: int = 0
EXIT_USAGE: int = 1
EXIT_DRIVER_CRASH: int = 2
EXIT_CLI_LINT_VIOLATION: int = 38

__all__ = [
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_DRIVER_CRASH",
    "EXIT_CLI_LINT_VIOLATION",
]
