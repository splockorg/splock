"""bin/_jsonl_log — Shared writer module for `_orchestrator_log.jsonl`.

Per implplan §C.impl.2 file tree. The `append_row` function in `writer.py`
is the SOLE entry point for writes across the splock substrate
(§A chain driver, §E `bin/update_orchestrator`, §K `bin/marker`, §L
`bin/route_issue`, §H `bin/morning-review`, §J eval CLIs, §M `bin/lessons`,
§N `bin/cli-lint`, §P `bin/intent`). No CLI opens the JSONL directly.

Public surface (importable from this package):
- `append_row(plan_dir, row, emitted_by)`         (writer.py)
- `KNOWN_WRITERS` frozenset                       (writers.py)
- `SUPPORTED_VERSIONS_LOG` list                   (writers.py)
- `UnregisteredWriterError`, `InvalidTransitionError` (writer.py)
- `_validate_or_truncate_last_line(path)`         (recovery.py; private)
- `read_rows(path)` line-by-line corruption-aware reader (reader.py)
- `wrap_reason(row_id, reason)`, `prompt_preamble()` (delimiter.py)
"""

from .writer import (
    InvalidTransitionError,
    UnregisteredWriterError,
    append_row,
)
from .writers import KNOWN_WRITERS, SUPPORTED_VERSIONS_LOG
from .recovery import ValidationResult, _validate_or_truncate_last_line
from .reader import iter_rows, read_rows
from .delimiter import prompt_preamble, wrap_reason

__all__ = [
    "append_row",
    "UnregisteredWriterError",
    "InvalidTransitionError",
    "KNOWN_WRITERS",
    "SUPPORTED_VERSIONS_LOG",
    "ValidationResult",
    "_validate_or_truncate_last_line",
    "iter_rows",
    "read_rows",
    "wrap_reason",
    "prompt_preamble",
]
