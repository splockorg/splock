"""Closed-enum exit codes for `bin/eli5`.

House conventions: 0/1 universal; 7 shared `atomic_write_failed`
family; 17 = SDK call failed with the SAME constant name as
`bin/_qa/exit_codes.py` (the code-17 collision is documented in the
acceptance-J `INTENTIONAL_COLLISIONS` allowlist under exactly this
name); 49 is eli5-owned in the first free slot of the full documented
registry (0..44 per §A.impl.3a, 45-48 taken by `bin/_fleet`).
"""

from __future__ import annotations

EXIT_OK = 0
"""Briefing rendered (and any requested files written)."""

EXIT_USAGE = 1
"""argparse usage error, `--prompt-file` without `--out`, or an invalid
flag combination."""

EXIT_ATOMIC_WRITE_FAILED = 7
"""`--out` / prompt-sheet atomic write failed (shared family)."""

EXIT_SDK_CALL_FAILED = 17
"""The single SDK call errored or returned empty text — the same code
(and name) `bin/qa` uses for SDK failure."""

EXIT_SUBJECT_UNREADABLE = 49
"""`--subject-file` exists as a flag but the file is missing,
unreadable, or empty after stripping. Distinct from usage (1) so
callers can tell a bad invocation from a bad subject artifact."""
