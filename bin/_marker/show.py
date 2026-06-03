"""`bin/marker show` subcommand (implplan §K.impl.3).

Exit codes:

  0 ok
  2 not-found
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from . import parser as marker_parser
from . import prefix as prefix_module


def run(
    *,
    marker_id: str,
    detail_only: bool = False,
    json_output: bool = False,
    repo_root: Optional[Path] = None,
) -> int:
    repo_root = repo_root or _repo_root()
    list_path = repo_root / prefix_module.LIST_PATH_REL

    entry = marker_parser.find_entry(list_path, marker_id)
    if entry is None:
        msg = f"not-found: {marker_id}"
        if json_output:
            print(json.dumps({"error": "not-found", "marker_id": marker_id}))
        else:
            print(msg, file=sys.stderr)
        return 2

    # Resolve detail file path
    detail_file_rel = entry.fields.get("detail_file", "")
    # The legacy "Detail file" field is typically a markdown link
    # `[`fname`](./fname)`; extract just the path
    detail_path = _resolve_detail_path(repo_root, detail_file_rel)

    if json_output:
        row = entry.to_schema_row()
        row["section"] = entry.section
        if detail_path and detail_path.exists():
            row["_detail_content"] = detail_path.read_text(encoding="utf-8")
        print(json.dumps(row, indent=2))
        return 0

    if detail_only:
        if detail_path and detail_path.exists():
            print(detail_path.read_text(encoding="utf-8"), end="")
        else:
            print(f"(detail file not present: {detail_file_rel})", file=sys.stderr)
            return 2
        return 0

    # Pretty print entry (raw_block lines lack trailing newlines)
    print("\n".join(entry.raw_block).rstrip())
    print()
    if detail_path and detail_path.exists():
        print("--- detail file content ---")
        print(detail_path.read_text(encoding="utf-8"), end="")
    return 0


def _resolve_detail_path(repo_root: Path, detail_field: str) -> Optional[Path]:
    """Extract the detail-file path from legacy `[`fname`](./fname)` form
    OR a schema-canonical relative path."""
    import re
    if not detail_field:
        return None
    # Legacy markdown link
    m = re.search(r"\[`([^`]+)`\]\(\./([^)]+)\)", detail_field)
    if m:
        basename = m.group(2)
        return repo_root / "docs" / "plans" / "scheduled_markers" / basename
    # Schema-canonical: docs/plans/scheduled_markers/<file>.md
    if detail_field.startswith("docs/plans/scheduled_markers/"):
        return repo_root / detail_field
    # Bare filename
    if detail_field.endswith(".md"):
        return repo_root / "docs" / "plans" / "scheduled_markers" / detail_field
    return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]
