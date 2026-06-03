"""bin/_chain_pause — pause/resume substrate for the chain driver (CCOR.1).

Per `docs/plans/_closed/ccor_1/implplan.md` T-4: this package owns the
`_chain_paused.lock` sentinel and its primitives. Sibling modules
(landing in T-5, T-6, T-7) will add the CLI entrypoints
(`bin/chain-pause`, `bin/chain-resume`) and the driver-side
pause-probe wiring.

The pause sentinel mirrors the H.9 1C `_chain_running.lock` mechanism
in `bin/_chain_overnight/sentinel.py` — same O_EXCL primitive, same
chain_id discipline on release, but it lives in its own package so
the pause/resume surface can evolve independently of the running-lock
discipline.

Public surface (re-exported below):

- `PAUSE_SENTINEL_FILENAME` — canonical sentinel basename.
- `PAUSE_SENTINEL_SCHEMA_VERSION` — payload schema version.
- `PauseAlreadyHeldError` / `PauseChainIdMismatchError` /
  `PauseSentinelCorruptError` — closed exception family.
- `acquire`, `release`, `release_if_present`, `read`, `is_held` —
  the five primitives used by the CLI and the driver.
"""

from .sentinel import (
    PAUSE_SENTINEL_FILENAME,
    PAUSE_SENTINEL_SCHEMA_VERSION,
    PauseAlreadyHeldError,
    PauseChainIdMismatchError,
    PauseSentinelCorruptError,
    acquire,
    is_held,
    read,
    release,
    release_if_present,
)


__all__ = [
    "PAUSE_SENTINEL_FILENAME",
    "PAUSE_SENTINEL_SCHEMA_VERSION",
    "PauseAlreadyHeldError",
    "PauseChainIdMismatchError",
    "PauseSentinelCorruptError",
    "acquire",
    "is_held",
    "read",
    "release",
    "release_if_present",
]
