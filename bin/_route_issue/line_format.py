"""Auto-producer line format parser + renderer + line-ID minter (implplan §L.impl.6).

Codifies plan §L.7 verbatim shape plus v1.3 codified extensions
(`line_id` + `status` sub-lines, both RATIFIED 2026-05-21 per L.impl.12 #1).

Top-line shape:

    - [<ISO-8601-Z>] [<plan-slug>] [<task_id>] [<emitted_by>] <gloss>

Sub-lines (indented two spaces, dash-prefix):

      - context: <ref>
      - blast_radius: <int>
      - related: <line_id_csv>
      - line_id: <id>
      - status: <enum>
      - promoted_to: <new-slug>   (only when status == "promoted")

`line_id` format: `oi_<ISO-Z>_<random4>` (4-char lowercase hex suffix).
`status` enum: open / promoted / resolved / superseded.

Hand-authored legacy lines lack `line_id`/`status`/`emitted_by`. Parser
yields them as `OutstandingEntry(legacy=True, line_id=None, ...)`;
subsequent CLI ops refuse to mutate legacy entries (no retro-format).
"""

from __future__ import annotations

import dataclasses
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


# `emitted_by` closed enum (parallels §K.impl.3 attribution stamping).
EMITTED_BY_CLOSED_ENUM = frozenset({
    "bin/route_issue",
    "bin/route_issue:fix-now",
    "bin/route_issue:outstanding",
    "bin/route_issue:marker",
    "bin/route_issue:tier-promote",
    "bin/route_issue:escalate",
    "bin/morning-review:route-outstanding",
})

STATUS_ENUM = frozenset({"open", "promoted", "resolved", "superseded"})


LINE_ID_RE = re.compile(
    r"^oi_\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z_[0-9a-f]{4}$"
)

TOP_LINE_RE = re.compile(
    r"^-\s+"
    r"\[(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\]\s+"
    r"\[(?P<plan_slug>[^\]]+)\]\s+"
    r"\[(?P<task_id>[^\]]*)\]\s+"
    r"\[(?P<emitted_by>[^\]]+)\]\s+"
    r"(?P<gloss>.+)$"
)

SUB_LINE_RE = re.compile(r"^  -\s+(?P<key>[a-z_]+):\s*(?P<val>.*)$")


class LineIdCollisionExceeded(RuntimeError):
    """Raised when two consecutive `mint_line_id` calls collide.

    Operationally never observed (4-char hex = 65536; throughput
    < 100/day; collision rate ~10^-4/year at uniform draw).
    """


@dataclasses.dataclass
class OutstandingEntry:
    """One outstanding-issues entry (top-line + sub-lines).

    Legacy entries (pre-CLI authored) have `legacy=True` and `line_id=None`.
    CLI-written entries always carry a `line_id` per the L.impl.6 contract.
    """
    timestamp: str
    plan_slug: str
    task_id: str
    emitted_by: str
    gloss: str
    context: Optional[str] = None
    blast_radius: Optional[int] = None
    related: Optional[str] = None
    line_id: Optional[str] = None
    status: str = "open"
    promoted_to: Optional[str] = None
    legacy: bool = False
    # Raw text for legacy entries — preserved byte-stable on re-render.
    raw_text: Optional[str] = None


def mint_line_id(now: Optional[datetime] = None) -> str:
    """Mint a fresh line-ID per §L.7 line 3983.

    Format: `oi_<ISO-Z>_<random4>` where random4 is 4-char lowercase hex.
    """
    when = now or datetime.now(timezone.utc)
    ts = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    suffix = secrets.token_hex(2)  # 4 hex chars
    return f"oi_{ts}_{suffix}"


def render_entry(entry: OutstandingEntry) -> str:
    """Render one entry back to MD (byte-stable round-trip).

    For legacy entries with `raw_text` populated, returns `raw_text`
    verbatim (the parser preserves the original bytes). For CLI-written
    entries, renders from structured fields.
    """
    if entry.legacy and entry.raw_text is not None:
        return entry.raw_text

    task_id = entry.task_id if entry.task_id is not None else ""
    lines: List[str] = [
        f"- [{entry.timestamp}] [{entry.plan_slug}] [{task_id}] "
        f"[{entry.emitted_by}] {entry.gloss}"
    ]
    if entry.context is not None:
        lines.append(f"  - context: {entry.context}")
    if entry.blast_radius is not None:
        lines.append(f"  - blast_radius: {entry.blast_radius}")
    if entry.related:
        lines.append(f"  - related: {entry.related}")
    if entry.line_id:
        lines.append(f"  - line_id: {entry.line_id}")
    lines.append(f"  - status: {entry.status}")
    if entry.status == "promoted" and entry.promoted_to:
        lines.append(f"  - promoted_to: {entry.promoted_to}")
    return "\n".join(lines)


def parse_outstanding_md(path: Path) -> List[OutstandingEntry]:
    """Parse `outstanding_issues.md` into a list of `OutstandingEntry` objects.

    Multi-line entry detection: top-line + indented sub-lines until next
    top-line OR blank line OR EOF. Legacy operator-authored prose blocks
    that don't match the top-line shape are dropped from the parsed list
    (CLI never mutates them; they're preserved by the file-rewrite path).
    """
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    entries: List[OutstandingEntry] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = TOP_LINE_RE.match(line)
        if not m:
            i += 1
            continue
        # Collect sub-lines
        sub_idx = i + 1
        sub_kvs: dict = {}
        raw_parts: List[str] = [line]
        while sub_idx < len(lines):
            sub = lines[sub_idx]
            sm = SUB_LINE_RE.match(sub)
            if not sm:
                break
            sub_kvs[sm.group("key")] = sm.group("val").strip()
            raw_parts.append(sub)
            sub_idx += 1
        emitted_by = m.group("emitted_by")
        legacy = emitted_by not in EMITTED_BY_CLOSED_ENUM
        blast = sub_kvs.get("blast_radius")
        try:
            blast_int = int(blast) if blast else None
        except ValueError:
            blast_int = None
        entry = OutstandingEntry(
            timestamp=m.group("ts"),
            plan_slug=m.group("plan_slug"),
            task_id=m.group("task_id"),
            emitted_by=emitted_by,
            gloss=m.group("gloss"),
            context=sub_kvs.get("context"),
            blast_radius=blast_int,
            related=sub_kvs.get("related"),
            line_id=sub_kvs.get("line_id"),
            status=sub_kvs.get("status", "open"),
            promoted_to=sub_kvs.get("promoted_to"),
            legacy=legacy,
            raw_text="\n".join(raw_parts) if legacy else None,
        )
        entries.append(entry)
        i = sub_idx
    return entries


def validate_emitted_by(emitted_by: str) -> None:
    """Refuse unattributed lines at write time per plan §L.7.

    Parser tolerates legacy entries (read-time); this guard is for the
    CLI's write path only.
    """
    if emitted_by not in EMITTED_BY_CLOSED_ENUM:
        raise ValueError(
            f"emitted_by={emitted_by!r} is not in the L.impl.6 closed enum. "
            f"Valid: {sorted(EMITTED_BY_CLOSED_ENUM)}"
        )


def validate_status(status: str) -> None:
    if status not in STATUS_ENUM:
        raise ValueError(
            f"status={status!r} not in closed enum {sorted(STATUS_ENUM)}"
        )


def validate_line_id(line_id: str) -> None:
    if not LINE_ID_RE.match(line_id):
        raise ValueError(
            f"line_id={line_id!r} does not match expected shape "
            f"`oi_<ISO-Z>_<random4>`"
        )
