"""Trigger-spec grammar — three closure-trigger shapes (implplan §K.impl.5).

Three accepted shapes:

1. `edit:<path-glob>:<edit-shape>`
   - edit-shapes: structural / any / add-or-update / rename / delete
   - example: `edit:extraction/investigator/actions.py:structural`
   - parsed `target` enum: `closure_trigger`

2. `date:<ISO-8601>`
   - example: `date:2026-08-15`
   - parsed `target` enum: `date`

3. `condition:<spec>`
   - spec sub-shapes: `SELECT …` | `exists:<path>` | `count:…` | `env:<VAR>=…`
   - example: `condition:exists:.claude/state/feature-flag-x.enabled`
   - parsed `target` enum: `condition`

Vague / open-ended values (`eventually`, `someday`, `TBD`, `later`,
`when ready`, `revisit when …`) are refused at parse time. Refusal codes
emitted by `refusal.py`.

The raw trigger string is preserved verbatim on a `Trigger spec:` line in
the detail file so downstream consumers (show, future apply) re-read it
without re-deriving.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Optional


# --- Regexes per the three shapes ---------------------------------------------

EDIT_SHAPES = ("structural", "any", "add-or-update", "rename", "delete")
EDIT_SHAPE_ALT = "|".join(EDIT_SHAPES)

EDIT_RE = re.compile(rf"^edit:([^:]+):({EDIT_SHAPE_ALT})$")
DATE_RE = re.compile(r"^date:(\d{4}-\d{2}-\d{2})$")
# condition: must start with one of the sub-shape keywords
CONDITION_RE = re.compile(
    r"^condition:(?P<spec>(SELECT\b.+|exists:\S+|count:.+|env:\S+=.+))$",
    re.IGNORECASE,
)

# Open-ended date sentinels (case-insensitive substring inside the trigger)
OPEN_DATE_SENTINELS = ("eventually", "someday", "tbd", "later", "when ready", "when-ready")


@dataclasses.dataclass
class ParsedTrigger:
    """Result of trigger_parser parse — target enum + structured pieces."""

    raw: str           # verbatim --trigger value
    target: str        # closure_trigger | date | condition
    # Shape-specific fields (only populated for the matching shape)
    edit_path: Optional[str] = None
    edit_shape: Optional[str] = None
    iso_date: Optional[str] = None
    condition_spec: Optional[str] = None


class TriggerParseError(ValueError):
    """Raised when a trigger string does not match any of the three shapes
    OR matches an open-ended sentinel."""

    def __init__(self, message: str, refusal_code: str):
        super().__init__(message)
        self.refusal_code = refusal_code


def parse(raw: str) -> ParsedTrigger:
    """Parse a raw --trigger string. Raises TriggerParseError on refusal.

    Returns ParsedTrigger with `target` set to the matching enum
    (`closure_trigger` / `date` / `condition`).
    """
    if not raw or not raw.strip():
        raise TriggerParseError(
            "Marker requires `--trigger`. Use one of: `edit:<path>:<shape>`, "
            "`date:YYYY-MM-DD`, `condition:<spec>`. See plan §K.5.",
            "R-TRIG-MISSING",
        )
    s = raw.strip()

    # Reject open-ended sentinels regardless of shape prefix
    lower = s.lower()
    for sentinel in OPEN_DATE_SENTINELS:
        # Match against the part AFTER any "date:" / "condition:" prefix too
        if lower == f"date:{sentinel}" or lower == sentinel or lower.startswith(f"date:{sentinel} ") or lower.startswith(f"date:{sentinel},"):
            raise TriggerParseError(
                "Open-ended date triggers refused (plan §K.6 + research_findings §D).",
                "R-TRIG-OPEN-DATE",
            )
        if lower.startswith(f"condition:{sentinel}") or lower.startswith(f"condition:when ready"):
            raise TriggerParseError(
                "Open-ended date triggers refused (plan §K.6 + research_findings §D).",
                "R-TRIG-OPEN-DATE",
            )

    # Edit shape
    m = EDIT_RE.match(s)
    if m:
        return ParsedTrigger(
            raw=s,
            target="closure_trigger",
            edit_path=m.group(1),
            edit_shape=m.group(2),
        )

    # Date shape
    m = DATE_RE.match(s)
    if m:
        return ParsedTrigger(
            raw=s,
            target="date",
            iso_date=m.group(1),
        )

    # Condition shape
    m = CONDITION_RE.match(s)
    if m:
        return ParsedTrigger(
            raw=s,
            target="condition",
            condition_spec=m.group("spec"),
        )

    # If it has a structured prefix but failed the regex, it's prose
    if s.startswith(("edit:", "date:", "condition:")):
        raise TriggerParseError(
            "Trigger is prose, not structured. Convert to one of three shapes.",
            "R-TRIG-PROSE",
        )

    # No structured prefix at all → prose
    raise TriggerParseError(
        "Trigger is prose, not structured. Convert to one of three shapes.",
        "R-TRIG-PROSE",
    )


def is_open_ended_date(raw: str) -> bool:
    """Helper used by tests + refusal table introspection."""
    lower = raw.lower().strip()
    for sentinel in OPEN_DATE_SENTINELS:
        if sentinel in lower:
            return True
    return False
