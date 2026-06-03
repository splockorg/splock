"""`bin/marker create` subcommand (implplan §K.impl.5 / §K.impl.7).

Exit codes (per §K.impl.3 table):

  0 ok
  2 schema-violation
  3 anti-pattern-refusal
  4 unknown-prefix
  5 flock-contention
  6 detail-write-failure
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import edit_block as edit_block_module
from . import log_emit
from . import parser as marker_parser
from . import prefix as prefix_module
from . import refusal
from . import schema as schema_module
from . import trigger_parser


def run(
    *,
    prefix: str,
    title: str,
    trigger: Optional[str],
    plan: Optional[str] = None,
    module: Optional[str] = None,
    data_needed: Optional[str] = None,
    context: Optional[str] = None,
    detail: Optional[str] = None,
    allow_na: bool = False,
    dry_run: bool = False,
    emitted_by: str = "bin/marker",
    json_output: bool = False,
    repo_root: Optional[Path] = None,
) -> int:
    """Execute create; returns exit code.

    Side effects (in order):
      1. Validate inputs against refusal table — refuses with code 3 on any hit.
      2. Validate prefix is registered — refuses with code 4 if not.
      3. Allocate next sequence number under flock on list.md.lock.
      4. Write detail file (BEFORE list.md update so an interrupted create
         leaves an orphan detail file rather than a list row pointing nowhere).
      5. Append entry to list.md (atomic temp + rename).
      6. Emit `marker_created` row to _orchestrator_log.jsonl.

    `repo_root` defaults to the resolved repository root; tests pass `tmp_path`.
    """
    repo_root = repo_root or _repo_root()
    list_path = repo_root / prefix_module.LIST_PATH_REL
    registry_path = repo_root / prefix_module.REGISTRY_PATH_REL

    # --- Step 1: refusal table -------------------------------------------------
    title_refusal = refusal.check_title(title)
    if title_refusal is not None:
        return _refuse(title_refusal, json_output, marker_args={
            "prefix": prefix, "title": title, "trigger": trigger,
        }, source_plan=plan)

    data_needed_text = data_needed if data_needed is not None else ""
    data_refusal = refusal.check_data_needed(data_needed_text, allow_na)
    if data_refusal is not None:
        return _refuse(data_refusal, json_output, marker_args={
            "prefix": prefix, "data_needed": data_needed_text,
        }, source_plan=plan)

    emit_refusal = refusal.check_emitted_by(emitted_by)
    if emit_refusal is not None:
        return _refuse(emit_refusal, json_output, marker_args={
            "emitted_by": emitted_by,
        }, source_plan=plan)

    if trigger is None or not trigger.strip():
        ref = refusal.Refusal(
            code=refusal.R_TRIG_MISSING,
            message=refusal.render(refusal.R_TRIG_MISSING),
            exit_code=3,
        )
        return _refuse(ref, json_output, marker_args={"prefix": prefix}, source_plan=plan)

    # Parse trigger spec
    try:
        parsed = trigger_parser.parse(trigger)
    except trigger_parser.TriggerParseError as e:
        ref = refusal.Refusal(
            code=e.refusal_code,
            message=str(e),
            exit_code=3,
        )
        return _refuse(ref, json_output, marker_args={
            "prefix": prefix, "trigger": trigger,
        }, source_plan=plan)

    # --- Step 2: prefix validation --------------------------------------------
    if not prefix_module.is_active_prefix(registry_path, prefix):
        # Compute Levenshtein suggestion
        active = prefix_module.active_prefixes(registry_path)
        sug = refusal.suggest_prefix(prefix, active)
        if sug is None:
            sug = "(none within distance ≤ 2)"
        msg = refusal.render(refusal.R_PREFIX_UNKNOWN, prefix=prefix, suggestion=sug)
        ref = refusal.Refusal(
            code=refusal.R_PREFIX_UNKNOWN,
            message=msg,
            exit_code=4,
        )
        return _refuse(ref, json_output, marker_args={
            "prefix": prefix, "suggestion": sug,
        }, source_plan=plan)

    # --- Step 3: allocate sequence under flock --------------------------------
    list_lock = list_path.parent / "list.md.lock"
    try:
        with prefix_module.flock_path(list_lock):
            seq = prefix_module.next_sequence_for(list_path, prefix)
            marker_id = f"{prefix}.{seq}"
            added_date = _today_iso()
            detail_basename = _detail_filename(marker_id, title)
            detail_rel = f"docs/plans/scheduled_markers/{detail_basename}"
            detail_abs = repo_root / detail_rel

            # --- Step 4: assemble + validate schema row ---------------------
            row = {
                "id": marker_id,
                "title": title,
                "added_date": added_date,
                "target": parsed.target,
                "source_plan": plan if plan else "null",
                "module": module if module else "cross-cutting",
                "data_needed": data_needed_text if data_needed_text else "n/a",
                "detail_file": detail_rel,
                "context": context if context else f"Created via bin/marker {added_date}.",
                "status": "active",
                "emitted_by": emitted_by,
            }
            try:
                schema_module.validate_row(row)
            except schema_module.SchemaError as e:
                return _emit_schema_violation(str(e), json_output, source_plan=plan)

            if dry_run:
                _print_dry_run(row, parsed.raw, detail_abs)
                return 0

            # --- Step 5: write detail file BEFORE list.md -------------------
            requires_edit_block = (parsed.target == "closure_trigger")
            edit_content = _resolve_detail_content(detail) if detail else ""
            detail_text = edit_block_module.render_detail_file(
                marker_id=marker_id,
                title=title,
                target=parsed.raw,
                source_plan=plan if plan else "null",
                added_date=added_date,
                emitted_by=emitted_by,
                context=row["context"],
                data_needed=row["data_needed"],
                edit_block_content=edit_content,
                requires_edit_block=requires_edit_block,
            )
            try:
                prefix_module.atomic_write(detail_abs, detail_text)
            except Exception as e:
                msg = f"detail-write-failure: {e}"
                if json_output:
                    print(json.dumps({"error": "detail-write-failure", "message": str(e)}))
                else:
                    print(msg, file=sys.stderr)
                return 6

            # --- Step 6: append to list.md ----------------------------------
            rendered = marker_parser.render_entry(row)
            current_text = list_path.read_text(encoding="utf-8") if list_path.exists() else ""
            new_text = marker_parser.append_active_entry(current_text, rendered)
            try:
                prefix_module.atomic_write(list_path, new_text)
            except Exception as e:
                msg = f"list-write-failure: {e}"
                if json_output:
                    print(json.dumps({"error": "atomic-write-failed", "message": str(e)}))
                else:
                    print(msg, file=sys.stderr)
                return 6

            # --- Step 7: emit log row ---------------------------------------
            log_emit.emit_marker_created(
                plan_dir=None,
                marker_id=marker_id,
                title=title,
                target=parsed.target,
                source_plan=plan,
                detail_file=detail_rel,
                marker_emitted_by=emitted_by,
            )

    except BlockingIOError:
        print("flock-contention: list.md.lock held; retry", file=sys.stderr)
        return 5
    except OSError as e:
        if "lock" in str(e).lower():
            print(f"flock-contention: {e}", file=sys.stderr)
            return 5
        raise

    # --- Stdout (success) ------------------------------------------------------
    if json_output:
        print(json.dumps({
            "result": "created",
            "marker_id": marker_id,
            "detail_file": detail_rel,
            "added_date": added_date,
            "target": parsed.target,
            "trigger_raw": parsed.raw,
        }))
    else:
        print(f"Created {marker_id}: {title}")
        print(f"  detail: {detail_rel}")
        print(f"  trigger: {parsed.raw} (target={parsed.target})")
    return 0


def _refuse(
    ref: refusal.Refusal,
    json_output: bool,
    marker_args: Optional[dict] = None,
    source_plan: Optional[str] = None,
) -> int:
    if json_output:
        print(json.dumps({
            "error": "anti-pattern-refusal",
            "refusal_code": ref.code,
            "refusal_message": ref.message,
        }))
    else:
        print(f"[{ref.code}] {ref.message}", file=sys.stderr)
    log_emit.emit_marker_create_refused(
        plan_dir=None,
        refusal_code=ref.code,
        refusal_message=ref.message,
        marker_args=marker_args,
        source_plan=source_plan,
    )
    return ref.exit_code


def _emit_schema_violation(message: str, json_output: bool, source_plan: Optional[str]) -> int:
    if json_output:
        print(json.dumps({"error": "schema-violation", "message": message}))
    else:
        print(f"SCHEMA_VIOLATION: {message}", file=sys.stderr)
    return 2


def _print_dry_run(row: dict, raw_trigger: str, detail_abs: Path) -> None:
    print("[dry-run] would create:")
    print(f"  id: {row['id']}")
    print(f"  title: {row['title']}")
    print(f"  added_date: {row['added_date']}")
    print(f"  target: {row['target']} (raw trigger: {raw_trigger})")
    print(f"  source_plan: {row['source_plan']}")
    print(f"  module: {row['module']}")
    print(f"  detail_file: {row['detail_file']} (path: {detail_abs})")
    print(f"  emitted_by: {row['emitted_by']}")


def _detail_filename(marker_id: str, title: str) -> str:
    """Compose `<lowercase_id>_<slugged_title>.md` per prefix_registry rule 5."""
    base_id = marker_id.lower().replace(".", "_")
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    slug_short = "_".join(slug.split("_")[:8])  # cap at first 8 words
    if not slug_short:
        slug_short = "marker"
    return f"{base_id}_{slug_short}.md"


def _resolve_detail_content(detail_arg: str) -> str:
    """Resolve --detail arg: either a file path (read content) or literal content."""
    # If it looks like an existing file path, read it
    p = Path(detail_arg)
    if p.exists() and p.is_file():
        return p.read_text(encoding="utf-8")
    return detail_arg


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]
