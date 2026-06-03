"""bin/_chain_resume — operator-resume CLI for the chain driver (CCOR.1 T-6).

Per `docs/plans/_closed/ccor_1/implplan.md` T-6: this package owns the
`bin/chain-resume` CLI entrypoint. It reads the `_chain_paused.lock`
sentinel acquired by `bin/chain-pause` (T-5), optionally injects
operator-supplied text for the next planner/opus spawn, stamps the
pause-end delta into the manifest's `total_paused_seconds`
accumulator, and releases the pause sentinel.

Sibling package: `bin/_chain_pause` (T-4 + T-5) owns the sentinel
primitives and the pause CLI; this package depends on those primitives
via direct import and adds the resume-side discipline:

- **R-orphan-detection** — sentinel present but driver dead/missing
  → exit 22 "crashed-during-pause"; sentinel NOT released (forensic
  state preserved for the operator).
- **R-stamp-before-release** — `manifest.stamp_pause_end` MUST be
  called before `pause_sentinel.release` so a stamp-failure unwinds
  the pause-end transaction without leaving the manifest desynced.
- **R-inject-size / R-inject-utf8 / R-inject-no-overwrite / R-inject-framing**
  — `--inject` validation contract.

Public surface: just the CLI entry point. No primitives are re-exported
from this package — callers wanting to read/release the pause sentinel
go through `bin._chain_pause.sentinel`.
"""
