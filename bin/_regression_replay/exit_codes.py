"""Exit codes for `bin/regression-replay` (§J.impl.6)."""

from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_UNSUPPORTED_SCHEMA = 5
EXIT_ATOMIC_WRITE_FAILED = 7
EXIT_CASE_NOT_FOUND = 22

ALL_CODES = frozenset(
    {
        EXIT_OK,
        EXIT_USAGE,
        EXIT_UNSUPPORTED_SCHEMA,
        EXIT_ATOMIC_WRITE_FAILED,
        EXIT_CASE_NOT_FOUND,
    }
)
