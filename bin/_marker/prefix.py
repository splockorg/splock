"""Prefix registry parser + sequence allocator (implplan §K.impl.7).

`prefix_registry.md` is the source of truth for active prefixes. This
module:

- Parses the `## Active prefixes` table → `{prefix: {expansion, domain,
  owner, examples}}`.
- Parses the `## Closed / retired prefixes` table — retired prefixes are
  NOT eligible for new markers (registry rule 6).
- Computes the next sequence number for a prefix by scanning `list.md`
  for `### <PREFIX>.<N>` headers (active + closed). Closed numbers are
  burned (registry rule 6).
- `register-prefix` mode appends a new row to the Active table in
  alphabetical position, holding `prefix_registry.md.lock` via flock.
"""

from __future__ import annotations

import dataclasses
import fcntl
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional

from . import parser as marker_parser


REGISTRY_PATH_REL = "docs/plans/scheduled_markers/prefix_registry.md"
LIST_PATH_REL = "docs/plans/scheduled_markers/list.md"


@dataclasses.dataclass
class PrefixRow:
    prefix: str
    expansion: str
    domain: str
    owner: str
    examples: str
    retired: bool = False


# Match a single Active prefix row. The first column is `` `PFX` `` (backticks).
ACTIVE_ROW_RE = re.compile(
    r"^\|\s*`([A-Z]{3,5})`\s*\|"
    r"\s*([^|]*)\|"
    r"\s*([^|]*)\|"
    r"\s*([^|]*)\|"
    r"\s*([^|]*)\|\s*$"
)


def parse_registry(path: Path) -> List[PrefixRow]:
    """Parse `prefix_registry.md` into a list of `PrefixRow` objects.

    Active prefixes parsed from rows under `## Active prefixes`; retired
    prefixes parsed from rows under `## Closed / retired prefixes`.
    """
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    rows: List[PrefixRow] = []
    section: Optional[str] = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped == "## Active prefixes":
            section = "active"
            continue
        if stripped == "## Closed / retired prefixes":
            section = "retired"
            continue
        if section and stripped.startswith("|"):
            # Skip header + separator rows
            if "Prefix" in raw_line and "Expansion" in raw_line:
                continue
            if re.match(r"^\|[\s\-:]+\|", raw_line):
                continue
            m = ACTIVE_ROW_RE.match(raw_line)
            if m:
                rows.append(
                    PrefixRow(
                        prefix=m.group(1),
                        expansion=m.group(2).strip(),
                        domain=m.group(3).strip(),
                        owner=m.group(4).strip(),
                        examples=m.group(5).strip(),
                        retired=(section == "retired"),
                    )
                )
    return rows


def active_prefixes(path: Path) -> List[str]:
    """Convenience: list of active prefix strings only."""
    return [r.prefix for r in parse_registry(path) if not r.retired]


def is_active_prefix(path: Path, prefix: str) -> bool:
    """Check whether `prefix` appears in the active set (not retired)."""
    rows = parse_registry(path)
    for r in rows:
        if r.prefix == prefix and not r.retired:
            return True
    return False


def is_retired_prefix(path: Path, prefix: str) -> bool:
    rows = parse_registry(path)
    for r in rows:
        if r.prefix == prefix and r.retired:
            return True
    return False


# --- Sequence allocation -------------------------------------------------------


def next_sequence_for(list_path: Path, prefix: str) -> int:
    """Return the next available sequence number for `prefix`.

    Reads `list.md` (active + closed), finds the highest existing N for
    this prefix, returns N+1. Closed numbers are burned (never reused).
    """
    current_max = marker_parser.max_sequence_for_prefix(list_path, prefix)
    return current_max + 1


# --- flock + atomic-write helpers ----------------------------------------------


@contextmanager
def flock_path(lockfile: Path) -> Iterator[None]:
    """Acquire LOCK_EX on `lockfile` (created lazily). Releases on exit."""
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lockfile), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def atomic_write(path: Path, content: str) -> None:
    """tempfile + os.replace atomic write (implplan cross-cutting + Finding 28).

    Writes to a sibling tempfile in the same directory, fsyncs, then
    `os.replace` to the final name. Same-directory placement ensures the
    rename(2) is atomic (POSIX guarantees same-filesystem).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


# --- register-prefix subcommand (append to prefix_registry.md) -----------------


def append_active_prefix(
    path: Path,
    prefix: str,
    expansion: str,
    domain: str,
    owner: str,
    examples: str = "",
) -> str:
    """Render the new `prefix_registry.md` text with `prefix` inserted in
    alphabetical position. Caller is responsible for the flock + atomic_write
    of the returned text.

    Raises ValueError if the prefix is already in active OR retired sections.
    """
    existing = parse_registry(path)
    for r in existing:
        if r.prefix == prefix:
            raise ValueError(
                f"Prefix `{prefix}` already registered "
                f"({'retired' if r.retired else 'active'})"
            )
    if not re.match(r"^[A-Z]{3,5}$", prefix):
        raise ValueError(
            f"Invalid prefix shape `{prefix}` — must be 3–5 uppercase letters"
        )

    text = path.read_text(encoding="utf-8")
    # Find the Active table; insert in alphabetical position
    lines = text.splitlines(keepends=True)
    # Locate active section start
    active_h2_idx: Optional[int] = None
    for i, line in enumerate(lines):
        if line.strip() == "## Active prefixes":
            active_h2_idx = i
            break
    if active_h2_idx is None:
        raise ValueError("prefix_registry.md missing '## Active prefixes' H2")

    # Find the end of the active table — first non-pipe line after the table
    # Skip header + separator first
    i = active_h2_idx + 1
    # advance through any blank lines + comment until the table header
    while i < len(lines) and not lines[i].lstrip().startswith("|"):
        i += 1
    # i now points at header row
    header_idx = i
    # advance through header + separator
    i += 1  # header row
    if i < len(lines) and re.match(r"^\|[\s\-:]+\|", lines[i]):
        i += 1  # separator
    table_data_start = i
    # find table end
    while i < len(lines) and lines[i].lstrip().startswith("|"):
        i += 1
    table_data_end = i  # exclusive

    new_row = (
        f"| `{prefix}` | {expansion} | {domain} | {owner} | {examples} |\n"
    )

    # Insert in alphabetical position among the data rows
    insert_at = table_data_start
    for j in range(table_data_start, table_data_end):
        m = ACTIVE_ROW_RE.match(lines[j])
        if not m:
            continue
        existing_p = m.group(1)
        if prefix < existing_p:
            insert_at = j
            break
        insert_at = j + 1

    new_lines = lines[:insert_at] + [new_row] + lines[insert_at:]
    return "".join(new_lines)
