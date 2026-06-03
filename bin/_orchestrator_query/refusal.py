"""Structured stderr emission for refusal paths (code_next_ready_pick T1).

Each refusal returns a structured JSON error to stderr + a stable exit
code from `exit_codes.py`. All refusal payloads use the `error` key as
the closed-enum discriminator (mirrors
`bin/_update_orchestrator/refusal.py`).

Closed-enum error keys:

| Error key | Source | Exit code |
|---|---|---|
| `slug_not_found` | F1.9 — `docs/plans/<slug>/` missing | 10 |
| `orchestrator_json_missing` | F1.9 — plan dir exists, JSON missing | 11 |
| `orchestrator_json_malformed` | F1.9 — JSON unparseable / non-array `tasks` | 12 |
| `state_shape_invalid` | F1.5 — `_state.json` `tasks` neither dict nor array | 13 |
| `no_ready_task_all_blocked` | F1.7 — verdict=all_blocked | 20 |
| `no_ready_task_all_wip` | F1.7 — verdict=all_wip | 21 |
| `no_ready_task_mixed` | F1.7 — verdict=mixed_no_ready | 22 |
| `plan_complete_all_done` | F1.7 — verdict=all_done | 23 |
"""

from __future__ import annotations

import json
import sys
from typing import Any


def emit_refusal(payload: dict, stream=None) -> None:
    """Serialize a refusal payload as JSON to stderr (one line).

    `payload` MUST include `error` (closed-enum discriminator); other
    fields are payload-specific (e.g., `slug`, `path`, `verdict`).
    """
    if stream is None:
        stream = sys.stderr
    line = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    stream.write(line + "\n")


def refusal_slug_not_found(slug: str, plan_dir: str) -> dict:
    return {
        "error": "slug_not_found",
        "slug": slug,
        "plan_dir": plan_dir,
        "hint": (
            "docs/plans/<slug>/ not found; verify slug spelling or run "
            "/plan <slug> to create the plan."
        ),
    }


def refusal_orchestrator_json_missing(slug: str, path: str) -> dict:
    return {
        "error": "orchestrator_json_missing",
        "slug": slug,
        "path": path,
        "hint": (
            "Plan dir exists but <slug>_orchestrator.json is missing; "
            "Phase 3 (orchestrator render) may be incomplete. Run "
            "/implplan <slug> to produce the orchestrator artifact."
        ),
    }


def refusal_orchestrator_json_malformed(slug: str, path: str, reason: str) -> dict:
    return {
        "error": "orchestrator_json_malformed",
        "slug": slug,
        "path": path,
        "reason": reason,
    }


def refusal_state_shape_invalid(slug: str, path: str, reason: str) -> dict:
    return {
        "error": "state_shape_invalid",
        "slug": slug,
        "path": path,
        "reason": reason,
        "hint": (
            "_state.json `tasks` must be dict (legacy) or array (schema-conformant); "
            "neither shape detected. See orch_status_render T4 for the schema."
        ),
    }


def refusal_no_ready_all_blocked(slug: str, blocked_ids: list[str]) -> dict:
    return {
        "error": "no_ready_task_all_blocked",
        "slug": slug,
        "blocked_ids": blocked_ids,
        "hint": (
            "Every non-terminal task is blocked. Unblock or cancel a blocker "
            "via bin/update_orchestrator before re-invoking /code."
        ),
    }


def refusal_no_ready_all_wip(slug: str, wip_ids: list[str]) -> dict:
    return {
        "error": "no_ready_task_all_wip",
        "slug": slug,
        "wip_ids": wip_ids,
        "hint": (
            "A wip task already owns the slot; resume the wip task "
            "explicitly with /code <slug> <task-id> instead of auto-pick."
        ),
    }


def refusal_no_ready_mixed(slug: str, summary: dict[str, list[str]]) -> dict:
    return {
        "error": "no_ready_task_mixed",
        "slug": slug,
        "summary": summary,
        "hint": (
            "Mixed terminal states with no ready slot. Inspect "
            "_state.json + _orchestrator.md for the picture."
        ),
    }


def refusal_plan_complete_all_done(slug: str, terminal_count: int) -> dict:
    return {
        "error": "plan_complete_all_done",
        "slug": slug,
        "terminal_count": terminal_count,
        "hint": (
            "All tasks are in a terminal status (done/cancelled/deferred). "
            "Plan is complete; nothing to pick."
        ),
    }
