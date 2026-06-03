"""Closed-enum exit codes for `bin/intent`.

Per implplan §A.impl.3a shared registry (lines 460-510). Codes 40 / 41 / 42
are §P-owned per A.impl.3a v1.4-parallel additions. Codes 0 / 1 / 2 / 7
are shared with the rest of the splock CLI surface.
"""

from __future__ import annotations

EXIT_OK = 0
"""Success."""

EXIT_USAGE = 1
"""Argparse usage error."""

EXIT_ENUM_VIOLATION = 2
"""Closed-enum refused (kind / status / dispatch_mode / resolution / event /
emitted_by). Per A.impl.3a scope-disambiguation discipline."""

EXIT_ATOMIC_WRITE_FAILED = 7
"""Atomic temp+rename failed during JSONL or daily-file append."""

EXIT_INTENT_COLLISION_DETECTED = 40
"""SERIALIZABLE check found one or more active sessions touching the area
or paths. `register` emits 40 AFTER writing the collision_log row +
intent.collision marker (collision is structurally captured before exit);
`check` emits 40 read-only (no write)."""

EXIT_INTENT_SESSION_NOT_FOUND = 41
"""`update` / `complete` / `pivot` invoked against a session_id that does
not exist (or is already terminal — closed-session updates also refused)."""

EXIT_INTENT_CLOSURE_TRIGGER_OPEN_ENDED = 42
"""`register --closure 'someday' / 'when_done' / 'eventually' / 'TBD'`
refused at parse time. Same anti-pattern refusal discipline as §K.6."""


__all__ = [
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_ENUM_VIOLATION",
    "EXIT_ATOMIC_WRITE_FAILED",
    "EXIT_INTENT_COLLISION_DETECTED",
    "EXIT_INTENT_SESSION_NOT_FOUND",
    "EXIT_INTENT_CLOSURE_TRIGGER_OPEN_ENDED",
]
