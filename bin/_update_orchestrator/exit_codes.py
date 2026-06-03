"""Closed-enum exit codes for `bin/update_orchestrator` (implplan §E.impl).

References the cross-CLI shared registry at §A.impl.3a. Codes 19, 29, 30,
33 are §E-specific. Codes 0, 1, 4, 5, 10 are shared with other
chain-orchestrated CLIs per the registry.

**2026-05-22 renumber:** EXIT_TASK_OUTSIDE_DEVELOP_PLAN_AUTHORITY was 18,
which collided with `_chain_overnight.EXIT_OPERATOR_KILLED = 18`
(userguide §13.3 documents 18 = operator-killed). Renumbered to 8 (free
code) to resolve the operator-facing ambiguity per acceptance suite
Finding 1 (`acceptance_pass2_findings.md`).

| Code | Family | Source |
|---|---|---|
| 0 | success | universal |
| 1 | usage | argparse / bad CLI surface |
| 4 | schema_rejected | shared (B.impl.4) |
| 5 | unsupported_schema_version | shared (B.impl.6) |
| 10 | phase_boundary_halt | shared (F.impl.8); reused for `done → wip` refusal in chain context |
| 19 | done_wip_rollback_refused | §E (E.impl.5) — operator-gate refusal without override |
| 29 | iteration_overflow_refused | §E (E.impl.4) — 21st-position append after sentinel set |
| 30 | dual_retry_cap_mutex_violated | §E (E.impl.4) — `retry_count` + `develop_plan_telemetry` co-populated |
| 8 | task_outside_develop_plan_authority | §E (E.impl.3) — `deferred`/`abandoned` task touched by `--from-develop-plan` (was 18, renumbered 2026-05-22) |
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_SCHEMA_REJECTED = 4
EXIT_UNSUPPORTED_SCHEMA_VERSION = 5
EXIT_PHASE_BOUNDARY_HALT = 10
EXIT_DONE_WIP_ROLLBACK_REFUSED = 19
EXIT_ITERATION_OVERFLOW_REFUSED = 29
EXIT_DUAL_RETRY_CAP_MUTEX_VIOLATED = 30
EXIT_TASK_OUTSIDE_DEVELOP_PLAN_AUTHORITY = 8

ALL_CODES = frozenset(
    {
        EXIT_OK,
        EXIT_USAGE,
        EXIT_SCHEMA_REJECTED,
        EXIT_UNSUPPORTED_SCHEMA_VERSION,
        EXIT_PHASE_BOUNDARY_HALT,
        EXIT_DONE_WIP_ROLLBACK_REFUSED,
        EXIT_ITERATION_OVERFLOW_REFUSED,
        EXIT_DUAL_RETRY_CAP_MUTEX_VIOLATED,
        EXIT_TASK_OUTSIDE_DEVELOP_PLAN_AUTHORITY,
    }
)
