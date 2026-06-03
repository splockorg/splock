"""`--type escalate` handler (implplan §L.impl).

Writes a structured handoff entry into the morning-review queue per plan
§L.3 + L.impl.12 #3 RATIFIED ("morning-review queue (current spec)").

Output path (per cross-cutting line 243 + plan §H.2 per-slug layout):
  docs/plans/<slug>/morning-review/escalation_<line_id_or_ts>.md

Since `bin/morning-review --internal-bootstrap-day` is owned by §H.impl
and does not yet exist at HEAD (per L.impl reading list), this handler
writes the file directly via atomic temp+rename. The §H.impl bootstrap
surface will absorb this call when it lands.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import line_format, log_emit
from .exit_codes import EXIT_ATOMIC_WRITE_FAILED, EXIT_OK


VALID_TRIGGER_SOURCES = frozenset({
    "blast_radius",
    "ddl_multi",
    "cross_vertical",
    "cross_repo",
    "operator_override_state",
    "operator_direct",
    "rubric_refused",
})


def _morning_review_dir(repo_root: Path, slug: str) -> Path:
    return repo_root / "docs" / "plans" / slug / "morning-review"


def _atomic_write(path: Path, content: str) -> None:
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


def _render_handoff(
    *,
    timestamp: str,
    line_id: str,
    slug: str,
    context: str,
    reason: str,
    trigger_source: str,
) -> str:
    """Render the structured handoff body. NOT free-text per plan §L.3."""
    return (
        f"# Escalation — {timestamp}\n\n"
        f"- **line_id:** {line_id}\n"
        f"- **plan_slug:** {slug}\n"
        f"- **trigger_source:** {trigger_source}\n"
        f"- **context:** {context}\n"
        f"- **reason:** {reason}\n"
        f"- **status:** open\n"
        f"\n"
        f"## Operator action required\n"
        f"\n"
        f"Triage via existing morning-review flow. Acknowledge or route to "
        f"another category (fix-now / outstanding / marker / tier-promote).\n"
    )


def run(
    *,
    reason: str,
    context: str,
    trigger_source: str = "operator_direct",
    dry_run: bool = False,
    json_output: bool = False,
    repo_root: Path,
    plan_slug: Optional[str] = None,
    now: Optional[datetime] = None,
) -> int:
    """Write an escalation entry to the morning-review queue.

    `trigger_source` defaults to `operator_direct` (operator invoked
    `--type escalate` directly); auto-trigger paths from main.py pass the
    detected trigger name (`blast_radius` etc.).
    """
    if trigger_source not in VALID_TRIGGER_SOURCES:
        # Tolerate unknown by remapping rather than refusing
        trigger_source = "operator_direct"

    slug = plan_slug or "splock"
    when = now or datetime.now(timezone.utc)
    timestamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    line_id = line_format.mint_line_id(when)

    safe_id = line_id.replace(":", "-")
    filename = f"escalation_{safe_id}.md"
    target = _morning_review_dir(repo_root, slug) / filename

    body = _render_handoff(
        timestamp=timestamp,
        line_id=line_id,
        slug=slug,
        context=context,
        reason=reason,
        trigger_source=trigger_source,
    )

    if dry_run:
        if json_output:
            print(json.dumps({
                "result": "dry-run",
                "type": "escalate",
                "line_id": line_id,
                "target_path": str(target.relative_to(repo_root)),
                "trigger_source": trigger_source,
            }))
        else:
            print(f"[dry-run] would write {target.relative_to(repo_root)}")
            print(f"[dry-run] line_id={line_id} trigger_source={trigger_source}")
        return EXIT_OK

    try:
        _atomic_write(target, body)
    except OSError as e:
        msg = f"atomic_write_failed (escalation): {e}"
        if json_output:
            print(json.dumps({"error": "atomic_write_failed", "message": str(e)}))
        else:
            print(msg, file=sys.stderr)
        return EXIT_ATOMIC_WRITE_FAILED

    plan_dir = log_emit.resolve_plan_dir(None, slug)
    log_emit.emit_row(
        plan_dir=plan_dir,
        plan_slug=slug,
        transition_from="ready",
        transition_to="blocked",
        reason=(
            f"escalation_triggered: line_id={line_id} "
            f"trigger_source={trigger_source} reason={reason!r}"
        ),
        emitted_by=log_emit.EMIT_ESCALATE,
        extra={
            "event_type": "escalation_triggered",
            "line_id": line_id,
            "trigger_source": trigger_source,
        },
    )

    if json_output:
        print(json.dumps({
            "result": "escalated",
            "type": "escalate",
            "line_id": line_id,
            "trigger_source": trigger_source,
            "target_path": str(target.relative_to(repo_root)),
        }))
    else:
        print(f"escalation written: {target.relative_to(repo_root)}")
        print(f"  line_id={line_id} trigger_source={trigger_source}")
    return EXIT_OK
