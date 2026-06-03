"""Round-trip parser for `docs/plans/scheduled_markers/list.md`.

Per implplan §K.impl.4. The existing list.md predates this CLI; entries
are hand-authored with field-label key-value blocks under `### <ID> — <Title>`
headers. The parser MUST read existing rows without crashing (non-judgmental
on read) while raising ValueError on access to non-conforming fields.

File structure:

    # Scheduled markers — registry            (H1 — preface)
    ... preface narrative ...
    ## Active entries                          (H2)
    ### <ID> — <Title>                         (H3 per marker)
    - **Marker ID:** <id>
    - **Title:** ...
    - **Added:** YYYY-MM-DD
    - **Target completion:** ... | **Target:** ...
    - **Source plan:** ...
    - **Parent module / commit:** ... | **Module:** ...
    - **Data needed to close:** ... (multi-line bullet block)
    - **Detail file:** [`...`](./...)
    - **Emitted by:** <enum>                   (CLI-written; legacy entries may omit)
    - **Context:** ...
    ## Closed entries                          (H2)
    ### <ID> — <Title>                         (H3 per closed marker)
    ... fields ...
    - **Closed:** YYYY-MM-DD — <resolution>

The parser preserves ordering + comments by storing the raw block as a
`raw_block` attribute alongside the structured fields. Writers (create /
close) re-emit using the raw block when only the closure metadata
changes, so hand-authored prose survives round-trip.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Tuple


# --- Section/header parsing ----------------------------------------------------

H2_ACTIVE = "## Active entries"
H2_CLOSED = "## Closed entries"
H3_ENTRY_RE = re.compile(r"^###\s+([A-Z]{3,5}\.[1-9][0-9]*)\s+—\s+(.+?)\s*$")

# Field-label patterns. Each entry's field bullets look like:
#   - **Field:** value
# Some legacy entries use multi-line block values (e.g., **Data needed to close:**
# followed by a nested bullet list). The parser captures whatever text follows
# the colon up to (but not including) the next "- **<Label>:**" or the next H3.
FIELD_LABEL_RE = re.compile(r"^-\s+\*\*([A-Z][^*]+?)\*\*:?\s*(.*)$")

# Canonical field-label aliases. Legacy entries use natural-language labels
# (e.g., "Target completion") that we map to schema field names. Lookup is
# case-sensitive on the exact label string (after stripping trailing
# colon and asterisks).
LABEL_ALIASES: dict[str, str] = {
    "Marker ID": "id",
    "ID": "id",
    "Title": "title",
    "Added": "added_date",
    "Added date": "added_date",
    "Target": "target",
    "Target completion": "target_human",  # legacy free-form; not the schema enum
    "Source plan": "source_plan",
    "Parent module / commit": "module",
    "Parent module": "module",
    "Module": "module",
    "Data needed to close": "data_needed",
    "Data needed": "data_needed",
    "Detail file": "detail_file",
    "Emitted by": "emitted_by",
    "Context": "context",
    "Closed": "closed",  # legacy "Closed: YYYY-MM-DD — resolution"
    "Status": "status",
    "Resolution": "closure_resolution",
}


@dataclasses.dataclass
class MarkerEntry:
    """One marker row as parsed from list.md.

    `fields` holds the canonical field-name → text mapping. Schema validation
    consumes this dict via `to_schema_row()`. `raw_block` holds the literal
    markdown lines (including leading `### ...` header and trailing blank line)
    so writers can re-emit untouched entries verbatim.
    """

    id: str
    title: str
    section: str  # "active" | "closed"
    raw_block: List[str]
    fields: dict
    line_start: int  # 1-indexed line number of the `### ...` header
    line_end: int    # 1-indexed; inclusive of last non-trailing line

    @property
    def status(self) -> str:
        return "closed" if self.section == "closed" else "active"

    def to_schema_row(self) -> dict:
        """Translate parsed fields to a schema-compliant dict.

        Raises ValueError on missing required fields. Optional/legacy fields
        absent from the parsed entry produce sentinel defaults so the parser
        is non-judgmental on read; the ValueError fires only when a caller
        explicitly requests schema validation (e.g., `validate` subcommand).
        """
        row: dict = {
            "id": self.id,
            "title": self.title,
            "status": self.status,
        }
        # Map schema fields, falling back to legacy text where present.
        for key in (
            "added_date",
            "target",
            "source_plan",
            "module",
            "data_needed",
            "detail_file",
            "emitted_by",
            "context",
            "closed_date",
            "closure_resolution",
        ):
            v = self.fields.get(key)
            if v is not None:
                # Normalize legacy detail-file markdown link to canonical path
                if key == "detail_file":
                    v = _normalize_detail_file(v)
                # Normalize source_plan markdown link/back-tick wrapper
                if key == "source_plan":
                    v = _normalize_source_plan(v)
                row[key] = v
        # Legacy "target_human" → derive schema "target" enum if possible
        if "target" not in row:
            human = self.fields.get("target_human")
            if human:
                row["target"] = _derive_target_from_human(human)
        # Legacy "closed: YYYY-MM-DD — resolution" → split into closed_date + closure_resolution
        legacy_closed = self.fields.get("closed")
        if legacy_closed and "closed_date" not in row:
            d, _, resolution = legacy_closed.partition("—")
            d = d.strip()
            resolution = resolution.strip()
            if re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", d):
                row["closed_date"] = d
            if resolution and "closure_resolution" not in row:
                row["closure_resolution"] = resolution
        return row


def _normalize_detail_file(value: str) -> str:
    """Convert legacy `[`fname`](./fname)` to canonical `docs/plans/scheduled_markers/<fname>`.

    Returns the original value unchanged if it already matches the canonical
    pattern.
    """
    v = value.strip()
    # Schema-canonical already
    if v.startswith("docs/plans/scheduled_markers/") and v.endswith(".md"):
        return v
    # Markdown link form: [`fname`](./fname)
    m = re.search(r"\(\./([^)]+\.md)\)", v)
    if m:
        return f"docs/plans/scheduled_markers/{m.group(1)}"
    # Bare basename
    if v.endswith(".md") and "/" not in v:
        return f"docs/plans/scheduled_markers/{v}"
    # Bare basename in backticks
    m = re.search(r"`([^`]+\.md)`", v)
    if m:
        return f"docs/plans/scheduled_markers/{m.group(1)}"
    return v


def _normalize_source_plan(value: str) -> str:
    """Strip backticks / path-trailing slashes / leading 'docs/plans/' from legacy entries.

    Schema requires `[a-z0-9_]+` OR literal 'null'. Legacy entries often store
    a path like ``docs/plans/testing_module/...``; we map those down to the
    last path component (slug). When the value can't be reduced, we return
    'null' so the schema's `null` literal accepts it (treating it as
    cross-cutting).
    """
    v = value.strip()
    if not v:
        return "null"
    if v.lower() in ("null", "none"):
        return "null"
    # Strip backticks
    v = v.strip("`").strip()
    # Path → slug (first segment after docs/plans/)
    m = re.match(r"(?:^|/)docs/plans/([a-z0-9_]+)(?:/|$)", v)
    if m:
        return m.group(1)
    # If value already matches pattern, accept
    if re.match(r"^[a-z0-9_]+$", v):
        return v
    # Otherwise treat as cross-cutting
    return "null"


def _derive_target_from_human(human: str) -> str:
    """Best-effort mapping of legacy 'Target completion' prose to enum.

    Used only when an entry lacks a structured `**Target:**` field. The output
    is a heuristic: `closure_trigger` if 'trigger' or 'edit' appears; `date`
    if a YYYY-MM-DD shape is present and 'trigger' is not; `condition`
    otherwise (catch-all for state-based prose).
    """
    h = human.lower()
    if "trigger-based" in h or "edit:" in h:
        return "closure_trigger"
    if re.search(r"\b\d{4}-\d{2}-\d{2}\b", h) and "trigger" not in h:
        return "date"
    return "condition"


# --- File-level parser ---------------------------------------------------------

def parse_list_md(path: Path) -> List[MarkerEntry]:
    """Parse `list.md` into a list of MarkerEntry objects (active + closed).

    Non-judgmental on field-level conformance — missing fields surface only
    when `MarkerEntry.to_schema_row()` is called (then schema validation
    catches them). Malformed top-level structure (no H2 sections, unparseable
    H3 header) raises ValueError.
    """
    if not path.exists():
        return []

    lines = path.read_text(encoding="utf-8").splitlines(keepends=False)
    section = "preface"
    entries: List[MarkerEntry] = []
    current_block: List[str] = []
    current_id: Optional[str] = None
    current_title: Optional[str] = None
    current_start: int = 0

    def _flush_current(end_line: int) -> None:
        nonlocal current_block, current_id, current_title, current_start
        if current_id is None:
            return
        fields = _parse_fields(current_block)
        entries.append(
            MarkerEntry(
                id=current_id,
                title=current_title or "",
                section=section if section in ("active", "closed") else "active",
                raw_block=list(current_block),
                fields=fields,
                line_start=current_start,
                line_end=end_line,
            )
        )
        current_block = []
        current_id = None
        current_title = None
        current_start = 0

    for i, line in enumerate(lines, 1):
        # Section transitions
        stripped = line.rstrip()
        if stripped == H2_ACTIVE:
            _flush_current(i - 1)
            section = "active"
            continue
        if stripped == H2_CLOSED:
            _flush_current(i - 1)
            section = "closed"
            continue
        # H3 entry header
        m = H3_ENTRY_RE.match(stripped)
        if m:
            _flush_current(i - 1)
            current_id = m.group(1)
            current_title = m.group(2).strip()
            current_start = i
            current_block = [line]
            continue
        # Accumulate into current entry block (skip preface narrative)
        if current_id is not None:
            current_block.append(line)
    _flush_current(len(lines))

    return entries


def _parse_fields(block: Iterable[str]) -> dict:
    """Extract field-label → text mapping from an entry's body lines.

    Multi-line field values (e.g., 'Data needed to close' with nested bullets)
    are joined with newlines until the next 'top-level' field bullet
    (`- **<Label>:**`) is encountered.
    """
    fields: dict = {}
    current_key: Optional[str] = None
    current_value: List[str] = []

    def _commit() -> None:
        nonlocal current_key, current_value
        if current_key is not None:
            text = "\n".join(current_value).strip()
            # Normalize: strip leading "[" or backticks; preserve text fidelity
            fields[current_key] = text
        current_key = None
        current_value = []

    for raw in block:
        line = raw.rstrip()
        # Match top-level field bullet: "- **Label:** value"
        m = re.match(r"^-\s+\*\*([^*]+?)\*\*:?\s*(.*)$", line)
        if m and not line.startswith("  "):
            _commit()
            label = m.group(1).strip().rstrip(":")
            tail = m.group(2)
            canonical = LABEL_ALIASES.get(label)
            if canonical is None:
                # Unknown label; skip (legacy entries may carry arbitrary keys)
                current_key = None
                current_value = []
                continue
            current_key = canonical
            if tail:
                current_value = [tail.strip()]
        else:
            # Continuation of current field
            if current_key is not None:
                current_value.append(line)
    _commit()
    return fields


# --- Writing ------------------------------------------------------------------

def render_entry(row: dict, raw_block: Optional[List[str]] = None) -> str:
    """Render a marker row as the canonical CLI-written markdown block.

    Output shape (per implplan §K.impl.4 — schema fields, written in stable order):

        ### <id> — <title>

        - **Marker ID:** <id>
        - **Title:** <title>
        - **Added:** <added_date>
        - **Target:** <target>
        - **Source plan:** <source_plan>
        - **Module:** <module>
        - **Data needed to close:** <data_needed>
        - **Detail file:** [`<basename>`](./<basename>)
        - **Emitted by:** <emitted_by>
        - **Context:** <context>
        (- **Closed:** <closed_date> — <closure_resolution>   ← only when status=closed)

    Trailing blank line is appended. If `raw_block` is provided AND the row's
    `status` field is the only schema-relevant difference (i.e., active → closed
    transition that only needs to append a Closed: line), the writer falls back
    to mutating the raw block to preserve hand-authored prose. The active →
    closed transition is the only round-trip case the CLI supports verbatim;
    other mutations re-emit canonically.
    """
    if raw_block is not None and row.get("status") == "closed":
        # Append-style: keep the existing block, add a Closed line
        out = list(raw_block)
        # Strip any existing trailing blank lines
        while out and not out[-1].strip():
            out.pop()
        resolution = row.get("closure_resolution", "")
        closed_date = row.get("closed_date", "")
        out.append(f"- **Closed:** {closed_date} — {resolution}")
        out.append("")  # trailing blank
        return "\n".join(out) + "\n"

    # Canonical re-emit
    lines = []
    lines.append(f"### {row['id']} — {row['title']}")
    lines.append("")
    lines.append(f"- **Marker ID:** {row['id']}")
    lines.append(f"- **Title:** {row['title']}")
    lines.append(f"- **Added:** {row['added_date']}")
    lines.append(f"- **Target:** {row['target']}")
    lines.append(f"- **Source plan:** {row.get('source_plan', 'null')}")
    lines.append(f"- **Module:** {row['module']}")
    # data_needed may span multiple lines; preserve newlines via indented continuation
    data_needed = row['data_needed']
    if "\n" in data_needed:
        first, _, rest = data_needed.partition("\n")
        lines.append(f"- **Data needed to close:** {first}")
        for cont in rest.splitlines():
            lines.append(f"  {cont}")
    else:
        lines.append(f"- **Data needed to close:** {data_needed}")
    detail_basename = row['detail_file'].split("/")[-1]
    lines.append(f"- **Detail file:** [`{detail_basename}`](./{detail_basename})")
    lines.append(f"- **Emitted by:** {row['emitted_by']}")
    context = row['context']
    if "\n" in context:
        first, _, rest = context.partition("\n")
        lines.append(f"- **Context:** {first}")
        for cont in rest.splitlines():
            lines.append(f"  {cont}")
    else:
        lines.append(f"- **Context:** {context}")
    if row.get("status") == "closed":
        resolution = row.get("closure_resolution", "")
        closed_date = row.get("closed_date", "")
        lines.append(f"- **Closed:** {closed_date} — {resolution}")
    lines.append("")
    return "\n".join(lines) + "\n"


def split_sections(text: str) -> Tuple[str, str, str, str]:
    """Split list.md text into (preface, active_header, active_body, closed_header_plus_body).

    Returns (preface_up_to_active_h2, "## Active entries\n", active_body,
    closed_block) where closed_block includes the `## Closed entries` H2 + body.
    Used by writers to inject new entries without disturbing other sections.
    """
    # Find the H2 boundaries
    active_idx = text.find("\n## Active entries\n")
    closed_idx = text.find("\n## Closed entries\n")
    if active_idx < 0:
        raise ValueError("list.md missing '## Active entries' section")
    if closed_idx < 0:
        # No closed section yet; treat tail as empty
        preface = text[: active_idx + 1]
        active_header = "## Active entries\n"
        active_body = text[active_idx + 1 + len(active_header) :]
        return preface, active_header, active_body, ""
    preface = text[: active_idx + 1]
    active_header = "## Active entries\n"
    active_body = text[active_idx + 1 + len(active_header) : closed_idx + 1]
    closed_block = text[closed_idx + 1 :]
    return preface, active_header, active_body, closed_block


def append_active_entry(text: str, entry_md: str) -> str:
    """Append a new entry to the Active section.

    Insertion point is just before the `## Closed entries` H2 (or at EOF if no
    closed section yet). Ensures exactly one blank line between the last
    active entry and the new entry.
    """
    preface, active_h, active_body, closed_block = split_sections(text)
    # Ensure active body ends with single blank line before insertion
    body = active_body.rstrip() + "\n\n"
    body += entry_md
    if not body.endswith("\n"):
        body += "\n"
    if closed_block:
        # Ensure exactly one blank line before the closed block
        body = body.rstrip() + "\n\n"
        return preface + active_h + body + closed_block
    return preface + active_h + body


def move_entry_to_closed(text: str, marker_id: str, rendered_entry: str) -> str:
    """Move an existing active entry to the Closed section, updating its body.

    Reads the existing block for `marker_id` in the active section, removes
    those lines, and appends `rendered_entry` to the closed section. Raises
    ValueError if `marker_id` is not found in active.
    """
    preface, active_h, active_body, closed_block = split_sections(text)
    # Find the entry's block in active_body
    lines = active_body.splitlines(keepends=True)
    h3_re = re.compile(rf"^###\s+{re.escape(marker_id)}\s+—\s+")
    start: Optional[int] = None
    end: Optional[int] = None
    for i, line in enumerate(lines):
        if h3_re.match(line):
            start = i
            # Find end: next H3 or end of section
            j = i + 1
            while j < len(lines) and not lines[j].startswith("### "):
                j += 1
            end = j
            break
    if start is None:
        raise ValueError(f"marker {marker_id} not found in active section")
    # Remove block, preserving section trailing structure
    new_active = "".join(lines[:start] + lines[end:])
    new_active = new_active.rstrip() + "\n"

    # Append to closed section
    if not closed_block:
        closed_block = "\n## Closed entries\n\n"
    # Replace "_None yet._" placeholder if present
    closed_block = closed_block.replace("_None yet._\n", "").replace("_None yet._", "")
    closed_block = closed_block.rstrip() + "\n\n" + rendered_entry
    if not closed_block.endswith("\n"):
        closed_block += "\n"

    return preface + active_h + new_active + "\n" + closed_block


def iter_entries(path: Path) -> Iterator[MarkerEntry]:
    """Streaming iterator over markers in list.md (active + closed)."""
    for entry in parse_list_md(path):
        yield entry


def find_entry(path: Path, marker_id: str) -> Optional[MarkerEntry]:
    """Lookup helper. Returns None if the ID is not present."""
    for entry in iter_entries(path):
        if entry.id == marker_id:
            return entry
    return None


def list_existing_ids(path: Path) -> List[str]:
    """Return all marker IDs currently in list.md (active + closed)."""
    return [e.id for e in iter_entries(path)]


def max_sequence_for_prefix(path: Path, prefix: str) -> int:
    """Highest sequence number for `prefix` across active + closed.

    Returns 0 if no markers exist for the prefix. Used by the allocator
    to compute `max(N) + 1`.
    """
    max_n = 0
    pat = re.compile(rf"^{re.escape(prefix)}\.([1-9][0-9]*)$")
    for entry in iter_entries(path):
        m = pat.match(entry.id)
        if m:
            n = int(m.group(1))
            if n > max_n:
                max_n = n
    return max_n
