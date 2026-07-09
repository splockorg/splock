"""bin/_chain_status — thin status renderer for the live overnight chain.

Per CCOR.1 T-8 + the orchestrator's `call_sites` note: this module is a
thin stub landing AHEAD of the L1 `chain_human_handoff` initiative,
sized to render at least the PAUSED state from the
`_chain_paused.lock` sentinel + the manifest's paused-time accumulator
+ the running sentinel. The L1 module owner is expected to extend this
surface (uptime, phase totals, cost rollup, etc.) when the full
human-handoff observability story lands; the renderer here is
deliberately minimal so a future expansion can merge without churn.

Public surface:

- `main` — CLI entry point (`python -m bin._chain_status.main` or via a
  `bin/chain-status` POSIX wrapper if one is added in a later commit).

Read-only by design: this module performs ZERO orchestrator state
mutations. It reads `_chain_paused.lock`, `_chain_running.lock`, and
`_chain_sessions.json` only; no log emission, no manifest writes, no
sentinel acquire/release.

NOTE: `__init__` intentionally does NOT re-export `main` from `.main`
so that `from bin._chain_status import main as chain_status_main`
imports the submodule (matching the chain_pause / chain_resume test
import convention) rather than the function — otherwise the function
would shadow the module name and `chain_status_main.main(...)` would
fail with AttributeError. Callers that want the function explicitly
can `from bin._chain_status.main import main`.
"""
