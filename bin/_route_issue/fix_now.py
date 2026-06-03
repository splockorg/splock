"""`--type fix-now` handler (implplan §L.impl).

Log-only; no artifact. Zero-blast-radius issues that the agent will fix
inline get a JSONL row stamped via §C writer for forensic visibility.
Cap counter is NOT incremented (per L.impl.7 `COUNTABLE_TYPES`).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from . import log_emit
from .exit_codes import EXIT_OK


def run(
    *,
    description: str,
    context: str,
    dry_run: bool = False,
    json_output: bool = False,
    repo_root: Optional[Path] = None,
    plan_slug: Optional[str] = None,
) -> int:
    """Stamp a `fix_now_logged` row and exit cleanly.

    Returns EXIT_OK unconditionally; no filesystem state changes.
    """
    if dry_run:
        if json_output:
            print(json.dumps({
                "result": "dry-run",
                "type": "fix-now",
                "description": description,
                "context": context,
            }))
        else:
            print(f"[dry-run] would log fix-now: {description} (context={context})")
        return EXIT_OK

    plan_dir = log_emit.resolve_plan_dir(None, plan_slug)
    log_emit.emit_row(
        plan_dir=plan_dir,
        plan_slug=plan_slug or "splock",
        transition_from="ready",
        transition_to="done",
        reason=f"fix_now_logged: {description} [context={context}]",
        emitted_by=log_emit.EMIT_FIX_NOW,
        extra={"event_type": "fix_now_logged"},
    )

    if json_output:
        print(json.dumps({
            "result": "logged",
            "type": "fix-now",
            "description": description,
            "context": context,
        }))
    else:
        print(f"fix-now logged: {description}")
    return EXIT_OK
