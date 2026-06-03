---
name: recon
description: Survey repository state for a plan slug and produce a read-only <slug>_recon.md evidence base. Use at the START of a new initiative, when the user says "recon X", "survey the codebase for X", "what's the current state of X", or before planning any non-trivial change. Read-only; never edits plan/orchestrator substrate.
---

# recon

Read-only repository survey for a plan slug. Enumerates the relevant surface
(components, couplings, traces, open questions) and writes the evidence base to
`docs/plans/<slug>/<slug>_recon.md`.

Operator entry: `/recon <slug> [free-text-tail]`. The tail may request a re-run
mode (append / new-file / overwrite) or carry a `--directive`.

This skill spawns the read-only `recon` subagent (see `agents/recon.md`). The
subagent must not edit any plan or orchestrator JSON.

Output artifact: `<slug>_recon.md` (slug-prefixed per the plan-slug naming
convention).
