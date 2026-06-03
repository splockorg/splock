"""`bin/marker list` subcommand (implplan §K.impl.3).

Exit codes:

  0 ok
  2 unknown-prefix-filter
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
    prefix_filter: Optional[str] = None,
    source_plan: Optional[str] = None,
    active: bool = True,
    closed: bool = False,
    all_entries: bool = False,
    json_output: bool = False,
    repo_root: Optional[Path] = None,
) -> int:
    repo_root = repo_root or _repo_root()
    list_path = repo_root / prefix_module.LIST_PATH_REL
    registry_path = repo_root / prefix_module.REGISTRY_PATH_REL

    if prefix_filter is not None:
        if not prefix_module.is_active_prefix(registry_path, prefix_filter) \
                and not prefix_module.is_retired_prefix(registry_path, prefix_filter):
            msg = f"unknown-prefix-filter: {prefix_filter}"
            if json_output:
                print(json.dumps({"error": "unknown-prefix-filter", "prefix": prefix_filter}))
            else:
                print(msg, file=sys.stderr)
            return 2

    # Filter set
    show_active = active or all_entries
    show_closed = closed or all_entries
    if not (closed or all_entries):
        show_active = True
    if closed and not all_entries:
        show_active = False

    entries = list(marker_parser.iter_entries(list_path))
    filtered = []
    for e in entries:
        if e.section == "active" and not show_active:
            continue
        if e.section == "closed" and not show_closed:
            continue
        if prefix_filter and not e.id.startswith(prefix_filter + "."):
            continue
        if source_plan:
            sp = e.fields.get("source_plan")
            if sp != source_plan:
                continue
        filtered.append(e)

    if json_output:
        out = []
        for e in filtered:
            row = e.to_schema_row()
            row["section"] = e.section
            out.append(row)
        print(json.dumps(out, indent=2))
    else:
        if not filtered:
            print("(no matching entries)")
            return 0
        for e in filtered:
            status_marker = "[CLOSED]" if e.section == "closed" else "[ACTIVE]"
            print(f"{status_marker} {e.id} — {e.title}")
    return 0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]
