"""Closed-enum refusal sets for `bin/intent` (P.impl.13).

`bin/intent` is the SOLE writer of `extraction.agent_sessions` /
`agent_session_collision_log` / `intent_event_log` /
`docs/intent/intent_local.jsonl`. Closed-enum semantics enforced HERE
(at the CLI layer) — VARCHAR(32) at storage rather than MySQL ENUM
per P.impl.4. This avoids the `_ENUM_CACHE` invalidation gotcha
(extraction/CLAUDE.md) since extending an enum is a code-only change.

Six closed sets:
  - KIND (agent_sessions.kind)
  - STATUS (agent_sessions.status)
  - DISPATCH_MODE (agent_session_collision_log.dispatch_mode)
  - RESOLUTION (agent_session_collision_log.resolution; None = unresolved)
  - EVENT (intent_event_log.event — six marker names)
  - EMITTED_BY (cross-table — subset of §C KNOWN_WRITERS v4)
"""

from __future__ import annotations

KIND: frozenset[str] = frozenset(
    {
        "interactive",
        "chain_overnight",
        "read_only_recon",
        "read_only_review",
    }
)

STATUS: frozenset[str] = frozenset(
    {
        "Planning",
        "Coding",
        "Reviewing",
        "Blocked",
        "Paused",
        "Done",
    }
)

DISPATCH_MODE: frozenset[str] = frozenset(
    {
        "interactive",
        "autonomous",
    }
)

RESOLUTION: frozenset[str] = frozenset(
    {
        "abandoned",
        "pivoted",
        "proceeded_override",
        "morning_review_deferred",
    }
)

EVENT: frozenset[str] = frozenset(
    {
        "intent.register",
        "intent.collision",
        "intent.update",
        "intent.complete",
        "intent.sync_pending",
        "intent.sync_resolved",
    }
)

EMITTED_BY: frozenset[str] = frozenset(
    {
        "bin/intent",
        "bin/intent:check",
        "bin/intent:register",
        "bin/intent:update",
        "bin/intent:complete",
        "bin/intent:list",
        "bin/intent:pivot",
        "bin/intent:doctor",
        "chain_driver_auto",
        # T2 (intent_session_auto_register): stamped by the SessionStart
        # hook's subprocess-call to `bin/intent register --emitted-by
        # session_start_auto` when an interactive Claude Code session
        # auto-registers. Per research §5.1 5-surface enumeration: this
        # is one of five allowlist surfaces that must contain
        # `session_start_auto` to avoid EXIT_ENUM_VIOLATION (2).
        "session_start_auto",
        # Stamped by the UserPromptSubmit hook's `bin/intent register
        # --upsert --emitted-by user_prompt_submit_auto` call. Backfills
        # rows for sessions that started pre-T1 and bumps last_activity_at
        # on every prompt for live sessions. Allowlist-required.
        "user_prompt_submit_auto",
    }
)

# Open-ended closure-trigger anti-patterns. Refused at register parse-time
# with EXIT_INTENT_CLOSURE_TRIGGER_OPEN_ENDED (42). Per P.impl.7 +
# research_findings_v1.md §D (HiL-Bench).
OPEN_ENDED_CLOSURE_TRIGGERS: frozenset[str] = frozenset(
    {
        "someday",
        "when_done",
        "eventually",
        "TBD",
    }
)


class EnumViolation(ValueError):
    """Raised when a CLI input fails closed-enum validation. Caller
    converts to EXIT_ENUM_VIOLATION (2) + structured stderr."""

    def __init__(self, field: str, value: str, valid: frozenset[str]) -> None:
        self.field = field
        self.value = value
        self.valid = sorted(valid)
        super().__init__(
            f"{field}={value!r} not in closed enum {self.valid}"
        )


def validate_kind(value: str) -> None:
    if value not in KIND:
        raise EnumViolation("kind", value, KIND)


def validate_status(value: str) -> None:
    if value not in STATUS:
        raise EnumViolation("status", value, STATUS)


def validate_dispatch_mode(value: str) -> None:
    if value not in DISPATCH_MODE:
        raise EnumViolation("dispatch_mode", value, DISPATCH_MODE)


def validate_resolution(value: str) -> None:
    if value not in RESOLUTION:
        raise EnumViolation("resolution", value, RESOLUTION)


def validate_event(value: str) -> None:
    if value not in EVENT:
        raise EnumViolation("event", value, EVENT)


def validate_emitted_by(value: str) -> None:
    if value not in EMITTED_BY:
        raise EnumViolation("emitted_by", value, EMITTED_BY)


# T4 (intent_session_auto_register): sentinel-area skip helper.
# Used by register.py to decide whether a §P.9 case-1 collision involving
# two `unscoped_interactive` sessions should halt or just emit an
# audit-trail row + allow. See bin/_intent/settings.py for the resolve
# helper + research Decision 2 for rationale.


def collision_is_sentinel_pair(
    incoming_area: str, lineage_snapshot: list[dict]
) -> bool:
    """Return True when every side of the collision carries the
    auto-register sentinel area ``unscoped_interactive``.

    Caller (register.py) combines this with the
    ``intent.sentinel_area_skip_collision`` knob to decide whether to
    halt or skip the collision halt. The audit-trail row is still
    emitted via the existing ``intent.collision`` marker so the skip is
    forensically visible.
    """
    # Local import to avoid circular: settings → refusal isn't needed,
    # but keep the sentinel literal centralized in bin._intent.settings.
    from . import settings as intent_settings
    sentinel = intent_settings.SENTINEL_AREA
    if incoming_area != sentinel:
        return False
    for m in lineage_snapshot or ():
        if m.get("target_system_area") != sentinel:
            return False
    return True


__all__ = [
    "KIND",
    "STATUS",
    "DISPATCH_MODE",
    "RESOLUTION",
    "EVENT",
    "EMITTED_BY",
    "OPEN_ENDED_CLOSURE_TRIGGERS",
    "EnumViolation",
    "validate_kind",
    "validate_status",
    "validate_dispatch_mode",
    "validate_resolution",
    "validate_event",
    "validate_emitted_by",
    "collision_is_sentinel_pair",
]
