"""Closed-enum exit codes for `bin/fleet` (shared-registry style).

Follows the cross-CLI conventions of §A.impl.3a: 0/1/2 universal, 7
shared `atomic_write_failed` family, and two fleet-owned codes in the
first free slots of the FULL documented registry (0..44 are taken —
per `bin/_planner/exit_codes.py`'s numbering note, the authoritative
free-slot source is the whole §A.impl.3a table, NOT the local module
scan). Codes are scope-disambiguated by binary (callers reading `$?`
know they invoked `bin/fleet`); `bin/fleet` is out-of-chain — the
chain driver never consumes its `$?`.
"""

from __future__ import annotations

EXIT_OK = 0
"""Subcommand completed (including deliberate no-ops: idempotent
re-migrate, `stage` hooks on a project that has not opted in)."""

EXIT_USAGE = 1
"""argparse usage error, invalid `--status`, or malformed seed input."""

EXIT_DRIVER_CRASH = 2
"""Unexpected exception inside the fleet dispatch."""

EXIT_ATOMIC_WRITE_FAILED = 7
"""State/hub/meta atomic write failed (shared family with §B / §F)."""

EXIT_FLEET_NOT_INITIALIZED = 45
"""The project has not opted in (no `docs/plans/_fleet/_fleet_meta.json`).
Emitted by mutating subcommands that require initialization (`update`,
`seed`, `migrate`, `render --write`). `bin/fleet init` creates it."""

EXIT_HUB_ANCHOR_MISSING = 46
"""`migrate` could not find a requested anchor, or `render --write`
found a hub whose `FLEET:*` markers are absent. In both cases the hub
file is left byte-identical (verify-before-swap)."""

EXIT_SPAWN_REFUSED = 47
"""`spawn`/`resume` refused before launching anything: the concurrency
cap is reached (all children draw one subscription pool), the slug dir
is missing, or the `claude` CLI is not on PATH."""

EXIT_NO_SESSION = 48
"""`resume` found no session handle in the slug's `_fleet_runs.jsonl`
ledger (nothing was ever spawned/completed for it, or the ledger rows
carrying `session_id` were lost)."""
