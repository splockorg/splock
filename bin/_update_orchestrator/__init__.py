"""`bin/update_orchestrator` substrate package (implplan §E.impl).

Hosts the base CLI (`bin/update_orchestrator <slug> <task_id> <status>`)
plus the `--from-develop-plan` subcommand mode that consumes develop-plan's
native 6-status enum and applies the deterministic 6 → 7 mapping defined
in §E.impl.3. The `bin/develop-plan-bypass-status` query CLI also lives
under this package (per §E.impl.10 decision 1: own bin/ entry, mirrors
`bin/marker` posture).

No LLM in the loop — entire surface is deterministic.
"""

from __future__ import annotations
