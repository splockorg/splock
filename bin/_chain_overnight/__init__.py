"""bin/_chain_overnight — chain driver substrate (splock v2.7 §A).

Per implplan §A.impl.2 file tree. CLI entry is `main.py`; POSIX shell
wrapper at `bin/chain-overnight`.

Public surface (importable from this package):
- `main` (the CLI entry function)
- `exit_codes` (closed-enum exit code registry per A.impl.3a)
- Sub-module facade re-exports below

The chain driver itself is a sequential orchestrator — it loops phases
2..5 in order, dispatching each via `phase_spawn.spawn_*` and emitting
transitions via `state_machine.emit_*`. The substrate is split into
focused modules so individual concerns can be tested in isolation
(see `tests/splock/test_chain_driver/`).
"""

from .exit_codes import (
    DRIVER_EMITTED_CODES,
    EXIT_ATOMIC_WRITE_FAILED,
    EXIT_CHAIN_FOREIGN_SENTINEL,
    EXIT_CHAIN_REFUSED,
    EXIT_DRIVER_CRASH,
    EXIT_GUARDRAIL_REFUSED,
    EXIT_OK,
    EXIT_OPERATOR_KILLED,
    EXIT_ORPHAN_DETECTED,
    EXIT_PHASE_BOUNDARY_HALT,
    EXIT_RETRY_EXCEEDED,
    EXIT_SEALED_PATH_REFUSED,
    EXIT_VERIFY_PLAN_REJECTED,
    EXIT_WALL_CLOCK_CAP,
)

__all__ = [
    "DRIVER_EMITTED_CODES",
    "EXIT_OK",
    "EXIT_DRIVER_CRASH",
    "EXIT_ATOMIC_WRITE_FAILED",
    "EXIT_SEALED_PATH_REFUSED",
    "EXIT_PHASE_BOUNDARY_HALT",
    "EXIT_WALL_CLOCK_CAP",
    "EXIT_GUARDRAIL_REFUSED",
    "EXIT_ORPHAN_DETECTED",
    "EXIT_VERIFY_PLAN_REJECTED",
    "EXIT_RETRY_EXCEEDED",
    "EXIT_OPERATOR_KILLED",
    "EXIT_CHAIN_REFUSED",
    "EXIT_CHAIN_FOREIGN_SENTINEL",
]
