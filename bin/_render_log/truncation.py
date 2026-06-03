"""120-char reason truncation + JSONL-line-pointer suffix.

Per implplan §C.impl.10 step 2c: truncate `reason` at 120 chars; if
truncated, append `... (full text in JSONL line <N>)` where `<N>` is the
1-indexed source JSONL line number.

Newline + pipe escaping happens BEFORE truncation so the 120-char limit
applies to the rendered form, not the raw bytes. This matches the
grep-friendliness contract (plan §C.2 closing line).
"""

from __future__ import annotations

REASON_TRUNCATION_LIMIT = 120


def escape_for_md(text: str) -> str:
    """Replace newlines with `\\n` literal and pipes with `\\|` literal.

    Per implplan §C.impl.10 step 2c. The escaped form keeps the
    single-line MD shape so `grep` doesn't break on multi-line reasons.
    """
    # Backslashes themselves: NOT escaped (per current contract; the
    # implplan does not list backslash among the escape rules).
    return text.replace("\n", "\\n").replace("|", "\\|")


def truncate_reason(reason: str, line_number: int) -> str:
    """Truncate to 120 chars; if shortened, append the line-pointer suffix.

    `line_number` is the 1-indexed source JSONL line number; included
    in the suffix verbatim so operators can `head -n N | tail -1` to
    retrieve full text.
    """
    escaped = escape_for_md(reason)
    if len(escaped) <= REASON_TRUNCATION_LIMIT:
        return escaped
    head = escaped[:REASON_TRUNCATION_LIMIT]
    return f"{head}... (full text in JSONL line {line_number})"
