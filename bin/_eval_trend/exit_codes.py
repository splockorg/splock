"""Exit codes for `bin/eval-trend` per §J.impl.10 + §A.impl.3a."""

from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_UNSUPPORTED_SCHEMA = 5
EXIT_ATOMIC_WRITE_FAILED = 7

ALL_CODES = frozenset(
    {EXIT_OK, EXIT_USAGE, EXIT_UNSUPPORTED_SCHEMA, EXIT_ATOMIC_WRITE_FAILED}
)
