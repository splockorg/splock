"""`bin/lessons` CLI — per-plan `lessons.md` schema + atomic-append writer.

Per implplan §M.impl.5 (Phase 4 §M context-hygiene remainder). Three
subcommands:

- ``bin/lessons add <slug>`` — append an entry (atomic, flock-guarded).
- ``bin/lessons query <slug>`` — read + filter entries; planner-consumable.
- ``bin/lessons list`` — enumerate lessons.md files across plans.

The planner subagent at Call 1 (§D.impl.6) invokes ``bin/lessons query``
to surface lessons learned from prior plans, avoiding re-attempted
rejected approaches.
"""

from __future__ import annotations

EMIT_BARE = "bin/lessons"
EMIT_ADD = "bin/lessons:add"
EMIT_QUERY = "bin/lessons:query"
EMIT_LIST = "bin/lessons:list"

__all__ = ["EMIT_BARE", "EMIT_ADD", "EMIT_QUERY", "EMIT_LIST"]
