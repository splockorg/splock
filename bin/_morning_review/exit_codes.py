"""Closed-enum exit codes for `bin/morning-review` (implplan §H.impl.3).

References the cross-CLI shared registry at §A.impl.3a. Codes 22 / 23 /
24 / 31 are §H-specific (allocated this pass). Codes 0 / 3 / 7 / 35 are
shared with other chain-orchestrated CLIs per the registry; code 3 is
propagated from `bin/marker create` via `route-marker`.

| Code | Constant | Meaning |
|---|---|---|
| 0 | `EXIT_OK` | universal success |
| 3 | (propagated `bin/marker` refusal) | route-marker passthrough |
| 7 | `EXIT_ATOMIC_WRITE_FAILED` | queue-file write tempfile or rename failed |
| 22 | `EXIT_QUEUE_ENTRY_NOT_FOUND` | no entry matches `<slug>, <task_id>` |
| 23 | `EXIT_TRIAGE_DOUBLE_CLOSE` | entry's `Operator triage:` mirror already terminal |
| 24 | `EXIT_ARCHIVE_MOVE_FAILED` | `gc` / implicit archive move failed |
| 31 | `EXIT_ABANDON_ARGS_MISSING` | `abandon` without `--confirm` AND/OR `--reason` |
| 35 | `EXIT_PROMOTION_IDEMPOTENT_NOOP` | `mark-for-eval` re-promotion (informational; v1.4) |
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_ATOMIC_WRITE_FAILED = 7
EXIT_QUEUE_ENTRY_NOT_FOUND = 22
EXIT_TRIAGE_DOUBLE_CLOSE = 23
EXIT_ARCHIVE_MOVE_FAILED = 24
EXIT_ABANDON_ARGS_MISSING = 31
EXIT_PROMOTION_IDEMPOTENT_NOOP = 35

ALL_CODES = frozenset(
    {
        EXIT_OK,
        EXIT_USAGE,
        EXIT_ATOMIC_WRITE_FAILED,
        EXIT_QUEUE_ENTRY_NOT_FOUND,
        EXIT_TRIAGE_DOUBLE_CLOSE,
        EXIT_ARCHIVE_MOVE_FAILED,
        EXIT_ABANDON_ARGS_MISSING,
        EXIT_PROMOTION_IDEMPOTENT_NOOP,
    }
)
