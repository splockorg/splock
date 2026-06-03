"""`--internal-mark-deferred` handler (implplan §H.impl.3 internal subcommand).

Called by `bin/_marker/morning_review_wrapper.py` (§K.impl.9 step 4) after
a successful `bin/marker create` invocation: updates the matching
morning-review entry's `Operator triage:` mirror to `[routed-marker]` and
emits the `morning_review_triage_route_marker` log row.

Slug + date are resolved by scanning open daily files (per §H.impl.3
internal-prefix subcommand spec). The matching entry must currently be in
`[pending]` mirror state.
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Optional

from . import entry_format, log_emit, queue_file
from .exit_codes import (
    EXIT_OK,
    EXIT_QUEUE_ENTRY_NOT_FOUND,
    EXIT_TRIAGE_DOUBLE_CLOSE,
)


def run(
    *,
    repo_root: pathlib.Path,
    task_id: str,
    marker_id: str,
    json_output: bool = False,
) -> int:
    """Scan open daily files; update mirror; emit log row.

    Returns:
      0 — success
      22 — entry not found in any open daily file
      23 — entry already terminal
    """
    # Scan every open daily file across slugs; first match wins.
    found: Optional[tuple[pathlib.Path, entry_format.Entry, str]] = None
    for daily in queue_file.iter_open_daily_files(repo_root, slug=None):
        text = queue_file.read_daily(daily)
        entry = entry_format.find_entry(text, task_id)
        if entry is None:
            continue
        # Slug from the daily file's parent-parent (docs/plans/<slug>/morning-review/X.md)
        slug = daily.parent.parent.name
        found = (daily, entry, slug)
        break

    if found is None:
        msg = (
            f"--internal-mark-deferred: no morning-review entry matches "
            f"task_id={task_id}"
        )
        _emit_error(msg, "queue_entry_not_found", json_output)
        return EXIT_QUEUE_ENTRY_NOT_FOUND

    daily, entry, slug = found
    if entry.triage_mirror in entry_format.TERMINAL_MIRRORS:
        msg = (
            f"--internal-mark-deferred: entry {task_id} in {daily.name} "
            f"already terminal ({entry.triage_mirror})"
        )
        _emit_error(msg, "triage_double_close", json_output)
        return EXIT_TRIAGE_DOUBLE_CLOSE

    updated = queue_file.update_mirror_atomic(daily, task_id, "[routed-marker]")
    if updated is None:
        # Race: another writer beat us. Treat as double-close.
        msg = f"--internal-mark-deferred: race on entry {task_id}; bailing"
        _emit_error(msg, "triage_double_close", json_output)
        return EXIT_TRIAGE_DOUBLE_CLOSE

    plan_dir = repo_root / "docs" / "plans" / slug
    log_emit.emit_triage(
        plan_dir,
        slug=slug,
        task_id=task_id,
        event_type=log_emit.EVT_TRIAGE_ROUTE_MARKER,
        sub_emitter=log_emit.EMIT_ROUTE_MARKER,
        reason=(
            f"morning_review_triage_route_marker task_id={task_id} "
            f"marker_id={marker_id}"
        ),
        pointer=marker_id,
    )
    if json_output:
        print(
            json.dumps(
                {
                    "ok": True,
                    "task_id": task_id,
                    "marker_id": marker_id,
                    "slug": slug,
                    "daily_file": str(daily),
                }
            )
        )
    return EXIT_OK


def _emit_error(msg: str, error_kind: str, json_output: bool) -> None:
    if json_output:
        print(json.dumps({"error": error_kind, "message": msg}), file=sys.stderr)
    else:
        print(msg, file=sys.stderr)
