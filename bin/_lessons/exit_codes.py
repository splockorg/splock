"""Closed-enum exit codes for `bin/lessons`.

Per implplan §A.impl.3a registry, codes 36-37 are §M-allocated (v1.4):

| Code | Family                              | Source |
|---|---|---|
| 0  | success                              | universal |
| 1  | usage                                | argparse / bad CLI surface |
| 2  | plan_not_found                       | shared (§L) |
| 4  | schema_rejected                      | shared (§B) |
| 7  | atomic_write_failed                  | shared (§B.impl.4) |
| 36 | lessons_required_field_missing       | §M (M.impl.5) |
| 37 | lessons_entry_malformed              | §M (M.impl.5) |
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_PLAN_NOT_FOUND = 2
EXIT_SCHEMA_REJECTED = 4
EXIT_ATOMIC_WRITE_FAILED = 7
EXIT_LESSONS_REQUIRED_FIELD_MISSING = 36
EXIT_LESSONS_ENTRY_MALFORMED = 37


ALL_CODES = frozenset(
    {
        EXIT_OK,
        EXIT_USAGE,
        EXIT_PLAN_NOT_FOUND,
        EXIT_SCHEMA_REJECTED,
        EXIT_ATOMIC_WRITE_FAILED,
        EXIT_LESSONS_REQUIRED_FIELD_MISSING,
        EXIT_LESSONS_ENTRY_MALFORMED,
    }
)
