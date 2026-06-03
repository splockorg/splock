"""Exit codes for `bin/eval-gate` per §J.impl.9 + §A.impl.3a."""

from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_UNSUPPORTED_SCHEMA = 5
EXIT_ATOMIC_WRITE_FAILED = 7
EXIT_EVAL_GATE_REGRESSION = 32
EXIT_BASELINE_MISSING = 33
EXIT_DATASET_EMPTY = 34

ALL_CODES = frozenset(
    {
        EXIT_OK,
        EXIT_USAGE,
        EXIT_UNSUPPORTED_SCHEMA,
        EXIT_ATOMIC_WRITE_FAILED,
        EXIT_EVAL_GATE_REGRESSION,
        EXIT_BASELINE_MISSING,
        EXIT_DATASET_EMPTY,
    }
)
