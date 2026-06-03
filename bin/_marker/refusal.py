"""Closed-enum refusal table for `bin/marker create` (implplan §K.impl.5).

Eight anti-pattern refusals, each with operator-readable message verbatim
per the implplan table. Refusal events emit to `_orchestrator_log.jsonl`
via `log_emit.py` with `emitted_by="bin/marker:create"` and a `reason`
field that includes the refusal code.

Stable string IDs (closed enum):

| Code | Anti-pattern |
|---|---|
| R-TRIG-MISSING | `--trigger` not supplied |
| R-TRIG-PROSE | `--trigger` value is free-form text, not structured |
| R-TRIG-OPEN-DATE | open-ended sentinel ('eventually', 'someday', 'TBD', 'later') |
| R-DATA-NA-UNAUTHORIZED | `data_needed=n/a` without `--allow-na` |
| R-PREFIX-UNKNOWN | prefix not in active registry; suggestion via Levenshtein |
| R-TITLE-QUESTION | title ends in `?` (questions are not markers) |
| R-TITLE-SPECULATIVE | title contains speculative-narrative phrases |
| R-EMIT-UNKNOWN | `emitted_by` value not in closed enum |
"""

from __future__ import annotations

import dataclasses
import re
from typing import List, Optional


# Refusal codes (closed enum — extensions require schema bump per implplan)
R_TRIG_MISSING = "R-TRIG-MISSING"
R_TRIG_PROSE = "R-TRIG-PROSE"
R_TRIG_OPEN_DATE = "R-TRIG-OPEN-DATE"
R_DATA_NA_UNAUTHORIZED = "R-DATA-NA-UNAUTHORIZED"
R_PREFIX_UNKNOWN = "R-PREFIX-UNKNOWN"
R_TITLE_QUESTION = "R-TITLE-QUESTION"
R_TITLE_SPECULATIVE = "R-TITLE-SPECULATIVE"
R_EMIT_UNKNOWN = "R-EMIT-UNKNOWN"

ALL_REFUSAL_CODES = frozenset(
    {
        R_TRIG_MISSING,
        R_TRIG_PROSE,
        R_TRIG_OPEN_DATE,
        R_DATA_NA_UNAUTHORIZED,
        R_PREFIX_UNKNOWN,
        R_TITLE_QUESTION,
        R_TITLE_SPECULATIVE,
        R_EMIT_UNKNOWN,
    }
)


# Operator-readable messages (verbatim from implplan §K.impl.5 table)
MESSAGES: dict[str, str] = {
    R_TRIG_MISSING: (
        "Marker requires `--trigger`. Use one of: `edit:<path>:<shape>`, "
        "`date:YYYY-MM-DD`, `condition:<spec>`. See plan §K.5."
    ),
    R_TRIG_PROSE: (
        "Trigger is prose, not structured. Convert to one of three shapes."
    ),
    R_TRIG_OPEN_DATE: (
        "Open-ended date triggers refused (plan §K.6 + research_findings §D)."
    ),
    R_DATA_NA_UNAUTHORIZED: (
        "`n/a` requires `--allow-na`; state the evidence/state needed or authorize."
    ),
    R_PREFIX_UNKNOWN: (
        "Prefix `{prefix}` not registered. Run `bin/marker register-prefix` first, "
        "or use closest match: `{suggestion}` (Levenshtein ≤ 2)."
    ),
    R_TITLE_QUESTION: (
        "Questions are not markers (§K.6). Convert to imperative, or route via "
        "`bin/route_issue`."
    ),
    R_TITLE_SPECULATIVE: (
        "Title speculative (§K.6 + research_findings §E). Restate concretely or "
        "route to recon."
    ),
    R_EMIT_UNKNOWN: (
        "Unknown `emitted_by` `{value}`."
    ),
}


# Speculative narrative patterns (R-TITLE-SPECULATIVE). Match against title.
SPECULATIVE_PATTERNS = (
    re.compile(r"\bmight\s+want\s+to\b", re.IGNORECASE),
    re.compile(r"\brevisit\s+when\b", re.IGNORECASE),
    re.compile(r"\bsomeday\s+consider\b", re.IGNORECASE),
    re.compile(r"\bmay\s+want\s+to\b", re.IGNORECASE),
    re.compile(r"\bperhaps\b", re.IGNORECASE),
)


@dataclasses.dataclass
class Refusal:
    """One refusal event — code + formatted message + exit code."""

    code: str
    message: str
    exit_code: int

    def to_log_row(self, marker_args: Optional[dict] = None) -> dict:
        """Render as a log row for `_orchestrator_log.jsonl` (subset)."""
        row = {
            "event_type": "marker_create_refused",
            "refusal_code": self.code,
            "refusal_message": self.message,
        }
        if marker_args:
            row["marker_args"] = marker_args
        return row


def render(code: str, **format_kwargs) -> str:
    """Render the operator-readable message for a refusal code.

    Format kwargs (when applicable):
      - R-PREFIX-UNKNOWN: `prefix=<pfx>`, `suggestion=<sug>`
      - R-EMIT-UNKNOWN: `value=<v>`
    """
    if code not in MESSAGES:
        raise KeyError(f"Unknown refusal code: {code}")
    msg = MESSAGES[code]
    if format_kwargs:
        try:
            msg = msg.format(**format_kwargs)
        except KeyError:
            pass  # leave unformatted if kwargs missing
    return msg


def check_title(title: str) -> Optional[Refusal]:
    """Refuse questions + speculative narratives. Returns None if title is OK."""
    if not title:
        return None
    if title.rstrip().endswith("?"):
        return Refusal(
            code=R_TITLE_QUESTION,
            message=render(R_TITLE_QUESTION),
            exit_code=3,
        )
    for pat in SPECULATIVE_PATTERNS:
        if pat.search(title):
            return Refusal(
                code=R_TITLE_SPECULATIVE,
                message=render(R_TITLE_SPECULATIVE),
                exit_code=3,
            )
    return None


def check_data_needed(data_needed: str, allow_na: bool) -> Optional[Refusal]:
    """Refuse `n/a` without `--allow-na`."""
    if data_needed.strip().lower() in {"n/a", "na", "n.a.", "none"} and not allow_na:
        return Refusal(
            code=R_DATA_NA_UNAUTHORIZED,
            message=render(R_DATA_NA_UNAUTHORIZED),
            exit_code=3,
        )
    return None


def check_emitted_by(value: str) -> Optional[Refusal]:
    """Refuse `emitted_by` values not in the closed enum.

    The closed enum lives in the schema (`schemas/marker_v1.schema.json`).
    We re-state it here for cheap pre-validation; schema validator is the
    source of truth on conflict.
    """
    valid = {
        "bin/marker",
        "bin/morning-review:route-marker",
        "bin/route_issue:route-marker",
        "agent",  # reserved bare value; v2.7 ships bare 'agent', sub-id deferred
    }
    if value not in valid:
        return Refusal(
            code=R_EMIT_UNKNOWN,
            message=render(R_EMIT_UNKNOWN, value=value),
            exit_code=3,
        )
    return None


def levenshtein(a: str, b: str) -> int:
    """Standard Levenshtein distance. Used for R-PREFIX-UNKNOWN suggestions."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    # Two-row DP
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[-1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


def suggest_prefix(unknown: str, active_prefixes: List[str], max_dist: int = 2) -> Optional[str]:
    """Find the closest active prefix within Levenshtein ≤ `max_dist`.

    Returns the closest match, or None if none qualify.
    """
    best: Optional[str] = None
    best_d = max_dist + 1
    for p in active_prefixes:
        d = levenshtein(unknown, p)
        if d < best_d:
            best_d = d
            best = p
    return best if best_d <= max_dist else None
