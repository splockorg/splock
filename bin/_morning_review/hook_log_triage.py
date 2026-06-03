"""Hook-log row categorization for the morning-review surface.

T5 (intent_session_auto_register) — research Decision 3 mandate:
``bin/morning-review`` extension to categorize ``intent.doctor`` rows
distinctly in the triage queue so failures surface in daily review.

This module is the categorization seam — it takes a hook-log JSONL row
(per :mod:`bin._hooks.log_emit`) and returns a closed-enum bucket name.
The morning-review CLI / `/morning-review` console surface can group
rows by bucket so operators see ``intent.doctor`` failures as a
distinct line item rather than being mixed into a generic "hook errors"
bucket.

The bucket enum is intentionally small. Adding a new bucket is an
additive-bump change tracked under the same v1.5-class schema-bump
policy as :mod:`bin._morning_review.entry_format` enums.

Public API:

* :data:`BUCKETS` — closed enum of bucket names.
* :func:`categorize` — row dict → bucket name.
* :func:`summarize` — list[row dict] → ``{bucket: count}``.
"""

from __future__ import annotations

from typing import Dict, Iterable, Mapping


# Closed enum of buckets. The `intent_doctor` bucket is the T5
# addition; the rest are heuristic fallbacks that scope the helper
# usefully even before more subsystems opt in to distinct categories.
BUCKETS: frozenset[str] = frozenset(
    {
        "intent_doctor",       # T5 — `intent.doctor` rows (subsystem)
        "intent_other",        # other `intent.*` / splock-intent hooks
        "session_start",       # splock-session-start.sh rows
        "morning_review",      # bin/morning-review's own emissions
        "other",               # catch-all
    }
)


def categorize(row: Mapping[str, object]) -> str:
    """Return the bucket name for one hook-log row.

    Row shape per :mod:`bin._hooks.log_emit`::

        {"ts": "<ISO>", "hook": "<name>", "action": "<verb>",
         "message": "<msg>", "session_id": "...", "slug": "...",
         "chain_id": "...", "phase": "...", "pid": <int>}

    Routing rules (first match wins):
      1. ``hook == "intent.doctor"`` → ``"intent_doctor"`` (T5).
      2. ``hook`` starts with ``"intent."`` or equals ``"intent"`` →
         ``"intent_other"``.
      3. ``hook == "splock-session-start"`` → ``"session_start"``.
      4. ``hook == "morning-review"`` → ``"morning_review"``.
      5. fallback → ``"other"``.

    Unknown / missing ``hook`` field defaults to ``"other"``.
    """
    hook_raw = row.get("hook")
    if not isinstance(hook_raw, str):
        return "other"
    hook = hook_raw.strip()
    if hook == "intent.doctor":
        return "intent_doctor"
    if hook == "intent" or hook.startswith("intent."):
        return "intent_other"
    if hook == "splock-session-start":
        return "session_start"
    if hook == "morning-review":
        return "morning_review"
    return "other"


def summarize(rows: Iterable[Mapping[str, object]]) -> Dict[str, int]:
    """Return ``{bucket: count}`` for an iterable of rows.

    Buckets with zero rows are included with count 0 so the operator
    sees a consistent shape across days.
    """
    counts: Dict[str, int] = {b: 0 for b in BUCKETS}
    for row in rows:
        b = categorize(row)
        counts[b] = counts.get(b, 0) + 1
    return counts


__all__ = ["BUCKETS", "categorize", "summarize"]
