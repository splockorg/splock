"""`lessons.md` markdown parser (per implplan §M.impl.5).

The per-entry shape is an H2-block:

    ## 2026-05-21 — One-line failure title

    **Task:** T3

    **Approach attempted:** <paragraph>

    **Failure mode:** <paragraph>

    **Why this approach was rejected:** <paragraph>

    **Re-attempt criteria:** <paragraph>

    **Source:** <pointer>

`parse_lessons_md(text)` returns a list of `LessonEntry` dataclasses,
one per H2 block. Hand-authored entries that are malformed (missing
required field, etc.) raise `LessonsEntryMalformedError` in strict
mode; the query CLI catches this for lenient surfacing per
§M.impl.5 (per-entry parser pattern matches §K.impl.4).
"""

from __future__ import annotations

import dataclasses
import re
from typing import Iterable


_DATE_TITLE_RE = re.compile(
    r"^##\s+(?P<date>\d{4}-\d{2}-\d{2})\s+[—–-]\s+(?P<title>.+?)\s*$"
)

# Required field labels (bolded prefixes). Map markdown label → JSON field.
_FIELDS: tuple[tuple[str, str], ...] = (
    ("Task", "task"),
    ("Approach attempted", "approach"),
    ("Failure mode", "failure_mode"),
    ("Why this approach was rejected", "rejection"),
    ("Re-attempt criteria", "reattempt"),
    ("Source", "source"),
)


_FIELD_LINE_RES: dict[str, re.Pattern[str]] = {
    json_field: re.compile(rf"^\*\*{re.escape(label)}:\*\*\s*(?P<value>.*)$")
    for label, json_field in _FIELDS
}


@dataclasses.dataclass
class LessonEntry:
    """One H2-block lesson entry."""

    date: str
    title: str
    task: str
    approach: str
    failure_mode: str
    rejection: str
    reattempt: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return dataclasses.asdict(self)


class LessonsEntryMalformedError(ValueError):
    """Raised when an H2 block cannot be parsed into a complete entry."""

    def __init__(self, message: str, *, h2_line_no: int | None = None):
        super().__init__(message)
        self.h2_line_no = h2_line_no


def _iter_h2_blocks(lines: list[str]) -> Iterable[tuple[int, list[str]]]:
    """Yield (h2_line_no, block_lines) pairs.

    block_lines includes the H2 header line and runs up to (but not
    including) the next H2 header or EOF.
    """
    current_start: int | None = None
    current_block: list[str] = []
    for line_no, line in enumerate(lines, start=1):
        if line.startswith("## "):
            # Flush prior block, if any.
            if current_start is not None:
                yield current_start, current_block
            current_start = line_no
            current_block = [line]
        else:
            if current_start is not None:
                current_block.append(line)
            # Lines before the first H2 are dropped (preamble).
    if current_start is not None:
        yield current_start, current_block


def _parse_one_block(h2_line_no: int, block_lines: list[str]) -> LessonEntry:
    header = block_lines[0]
    header_m = _DATE_TITLE_RE.match(header)
    if not header_m:
        raise LessonsEntryMalformedError(
            f"H2 header at line {h2_line_no} does not match "
            f"`## <ISO-date> — <title>` shape: {header!r}",
            h2_line_no=h2_line_no,
        )
    date = header_m.group("date")
    title = header_m.group("title").strip()

    found: dict[str, str] = {}
    # Walk body lines; for each label match, accumulate trailing paragraph
    # until the next label/H2/EOF.
    body = block_lines[1:]
    i = 0
    while i < len(body):
        line = body[i]
        matched_field: str | None = None
        for json_field, regex in _FIELD_LINE_RES.items():
            m = regex.match(line)
            if m:
                matched_field = json_field
                value = m.group("value").strip()
                # Accumulate continuation lines until blank-blank or next label.
                j = i + 1
                continuation: list[str] = []
                while j < len(body):
                    next_line = body[j]
                    # Stop on any field-label or H2 boundary.
                    if any(r.match(next_line) for r in _FIELD_LINE_RES.values()):
                        break
                    if next_line.startswith("## "):
                        break
                    continuation.append(next_line)
                    j += 1
                if continuation:
                    extra = "\n".join(continuation).strip()
                    if extra:
                        value = (value + "\n" + extra).strip() if value else extra
                found[json_field] = value
                i = j
                break
        if matched_field is None:
            i += 1

    # Validate all required fields are present + non-empty.
    missing = [f for _, f in _FIELDS if not found.get(f, "").strip()]
    if missing:
        raise LessonsEntryMalformedError(
            f"H2 block at line {h2_line_no} ({title!r}) missing required "
            f"field(s): {missing}",
            h2_line_no=h2_line_no,
        )

    return LessonEntry(
        date=date,
        title=title,
        task=found["task"].strip(),
        approach=found["approach"].strip(),
        failure_mode=found["failure_mode"].strip(),
        rejection=found["rejection"].strip(),
        reattempt=found["reattempt"].strip(),
        source=found["source"].strip(),
    )


def parse_lessons_md(text: str, *, lenient: bool = False) -> list[LessonEntry]:
    """Parse `lessons.md` content into a list of `LessonEntry`.

    Parameters
    ----------
    text : str
        Raw lessons.md content. Empty string returns empty list.
    lenient : bool, default False
        If True, malformed H2 blocks are silently dropped (with a
        warning callable via the `warnings` module). If False, raise
        `LessonsEntryMalformedError` on first malformed block.
    """
    if not text:
        return []
    lines = text.splitlines()
    entries: list[LessonEntry] = []
    for h2_line_no, block in _iter_h2_blocks(lines):
        try:
            entries.append(_parse_one_block(h2_line_no, block))
        except LessonsEntryMalformedError:
            if lenient:
                import warnings

                warnings.warn(
                    f"lessons.md: dropped malformed block at line {h2_line_no}",
                    stacklevel=2,
                )
                continue
            raise
    return entries


__all__ = [
    "LessonEntry",
    "LessonsEntryMalformedError",
    "parse_lessons_md",
]
