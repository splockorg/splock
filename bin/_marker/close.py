"""`bin/marker close` subcommand (implplan §K.impl.3 / §K.impl.4).

Exit codes:

  0 ok
  2 not-found
  3 already-closed
  5 flock-contention
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import log_emit
from . import parser as marker_parser
from . import prefix as prefix_module


def run(
    *,
    marker_id: str,
    resolution: str,
    dry_run: bool = False,
    json_output: bool = False,
    repo_root: Optional[Path] = None,
) -> int:
    repo_root = repo_root or _repo_root()
    list_path = repo_root / prefix_module.LIST_PATH_REL

    if not list_path.exists():
        print(f"not-found: list.md does not exist at {list_path}", file=sys.stderr)
        return 2

    entry = marker_parser.find_entry(list_path, marker_id)
    if entry is None:
        msg = f"not-found: {marker_id}"
        if json_output:
            print(json.dumps({"error": "not-found", "marker_id": marker_id}))
        else:
            print(msg, file=sys.stderr)
        return 2

    if entry.section == "closed":
        msg = f"already-closed: {marker_id}"
        if json_output:
            print(json.dumps({"error": "already-closed", "marker_id": marker_id}))
        else:
            print(msg, file=sys.stderr)
        return 3

    if not resolution or not resolution.strip():
        # Treat empty as schema violation rather than allow it
        print("close requires --resolution \"...\"", file=sys.stderr)
        return 3

    closed_date = _today_iso()

    if dry_run:
        print(f"[dry-run] would close {marker_id}")
        print(f"  closed_date: {closed_date}")
        print(f"  closure_resolution: {resolution}")
        return 0

    list_lock = list_path.parent / "list.md.lock"
    try:
        with prefix_module.flock_path(list_lock):
            # Re-read inside the lock to avoid races
            current_text = list_path.read_text(encoding="utf-8")
            current_entry = marker_parser.find_entry(list_path, marker_id)
            if current_entry is None:
                return 2
            if current_entry.section == "closed":
                return 3

            # Render the closed entry by extending the existing raw block
            closed_row = current_entry.to_schema_row()
            closed_row["status"] = "closed"
            closed_row["closed_date"] = closed_date
            closed_row["closure_resolution"] = resolution

            rendered = marker_parser.render_entry(
                closed_row,
                raw_block=current_entry.raw_block,
            )
            new_text = marker_parser.move_entry_to_closed(
                current_text, marker_id, rendered
            )
            prefix_module.atomic_write(list_path, new_text)

            # Emit log
            source_plan = current_entry.fields.get("source_plan")
            if source_plan in ("null", None, ""):
                source_plan = None
            log_emit.emit_marker_closed(
                plan_dir=None,
                marker_id=marker_id,
                resolution=resolution,
                source_plan=source_plan,
            )

    except BlockingIOError:
        print("flock-contention: list.md.lock held; retry", file=sys.stderr)
        return 5

    if json_output:
        print(json.dumps({
            "result": "closed",
            "marker_id": marker_id,
            "closed_date": closed_date,
            "closure_resolution": resolution,
        }))
    else:
        print(f"Closed {marker_id} ({closed_date}): {resolution}")
    return 0


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]
