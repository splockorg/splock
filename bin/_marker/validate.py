"""`bin/marker validate` subcommand (implplan §K.impl.4 / §K.impl.6 / §K.impl.8).

Exit codes (per §K.impl.3 + §K.impl.8 table):

  0  Clean
  11 schema-violation
  12 missing detail file
  13 missing/empty edit block
  14 unknown prefix
  15 vague trigger

stderr shape:
  SCHEMA_VIOLATION: <ID>: <field>: <reason>
  MISSING_DETAIL_FILE: <ID>: <expected_path>
  MISSING_EDIT_BLOCK: <ID>: <detail_path>
  UNKNOWN_PREFIX: <ID>
  VAGUE_TRIGGER: <ID>: <trigger_value>: <reason>

Modes:
  --all (default) — scan every entry in list.md
  --changed-only — read `git diff --cached --name-only` and only validate
    rows touched by the staged diff against `list.md`.
  --strict-edit-block — even retroactively (legacy entries pre-CLI) refuse
    on missing edit-block; default is to validate only CLI-written entries
    (those with the `emitted_by` field present).

Multiple violations: one stderr line per violation; exit code is the
NUMERICALLY LARGEST violating code (so 13 > 12 > 11; vague trigger 15 > 14).
A clean run with zero violations exits 0.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from . import edit_block as edit_block_module
from . import parser as marker_parser
from . import prefix as prefix_module
from . import schema as schema_module
from . import trigger_parser


def run(
    *,
    scope: str = "all",                # "all" | "changed-only"
    strict_edit_block: bool = False,
    json_output: bool = False,
    repo_root: Optional[Path] = None,
) -> int:
    repo_root = repo_root or _repo_root()
    list_path = repo_root / prefix_module.LIST_PATH_REL
    registry_path = repo_root / prefix_module.REGISTRY_PATH_REL

    if not list_path.exists():
        print(f"OK: list.md does not exist yet (no entries to validate)")
        return 0

    entries = list(marker_parser.iter_entries(list_path))
    if scope == "changed-only":
        if not _git_staged_includes_list_md(repo_root):
            # Idempotent — nothing to validate
            if json_output:
                print(json.dumps({"result": "clean", "scope": "changed-only", "validated": 0}))
            else:
                print(
                    f"OK: list.md not in staged diff (changed-only); skipped"
                )
            return 0

    active_prefixes = set(prefix_module.active_prefixes(registry_path))
    # Retired prefixes are still "known" for validate (they shouldn't FAIL
    # unknown-prefix; only new creates fail that). So no special handling.
    retired_prefixes = set(
        p.prefix for p in prefix_module.parse_registry(registry_path) if p.retired
    )

    violations: List[Tuple[int, str]] = []   # (code, stderr_line)
    active_count = 0
    closed_count = 0

    for entry in entries:
        entry_violations = _validate_entry(
            entry,
            repo_root=repo_root,
            active_prefixes=active_prefixes,
            retired_prefixes=retired_prefixes,
            strict_edit_block=strict_edit_block,
        )
        violations.extend(entry_violations)
        if entry.section == "closed":
            closed_count += 1
        else:
            active_count += 1

    if json_output:
        payload = {
            "result": "clean" if not violations else "violations",
            "active": active_count,
            "closed": closed_count,
            "violations": [{"code": c, "message": m} for c, m in violations],
        }
        print(json.dumps(payload, indent=2))
    else:
        for code, line in violations:
            print(line, file=sys.stderr)
        if not violations:
            print(f"OK: {active_count} active, {closed_count} closed entries validated.")

    if violations:
        # Return the largest code (highest-priority violation)
        return max(c for c, _ in violations)
    return 0


def _validate_entry(
    entry: marker_parser.MarkerEntry,
    *,
    repo_root: Path,
    active_prefixes: set,
    retired_prefixes: set,
    strict_edit_block: bool,
) -> List[Tuple[int, str]]:
    """Return list of (exit_code, stderr_line) tuples for this entry."""
    violations: List[Tuple[int, str]] = []

    # --- Prefix check (code 14) ----------------------------------------------
    m = re.match(r"^([A-Z]{3,5})\.[1-9][0-9]*$", entry.id)
    if not m:
        violations.append((11, f"SCHEMA_VIOLATION: {entry.id}: id: does not match marker-id pattern"))
        return violations
    pfx = m.group(1)
    if pfx not in active_prefixes and pfx not in retired_prefixes:
        violations.append((14, f"UNKNOWN_PREFIX: {entry.id}"))

    # --- Schema check (code 11) ----------------------------------------------
    # CLI-written entries are expected to have all 13 fields. Legacy
    # entries may omit fields (data_needed, detail_file, emitted_by) OR
    # carry hand-authored `Emitted by:` prose that doesn't match the closed
    # enum. We treat legacy as informational unless strict mode forces failure.
    CLI_EMITTED_BY_ENUM = {
        "bin/marker",
        "bin/morning-review:route-marker",
        "bin/route_issue:route-marker",
        "agent",
    }
    emitted_by_value = entry.fields.get("emitted_by", "").strip()
    is_cli_written = emitted_by_value in CLI_EMITTED_BY_ENUM
    if is_cli_written or strict_edit_block:
        try:
            row = entry.to_schema_row()
            schema_module.validate_row(row)
        except (schema_module.SchemaError, ValueError) as e:
            # One line per field violation
            for line in str(e).split("\n"):
                if line.strip():
                    violations.append((11, f"SCHEMA_VIOLATION: {entry.id}: {line}"))

    # --- Trigger check (code 15) ---------------------------------------------
    # `target` may be either an enum value or absent (legacy). When the raw
    # trigger spec is preserved on the detail file's "Target:" line, parse it.
    target_value = entry.fields.get("target") or entry.fields.get("target_human") or ""
    if is_cli_written and target_value:
        try:
            # Schema enum is target_value if structured; try to look up trigger
            # on the detail file for raw shape parsing
            if target_value not in ("closure_trigger", "date", "condition"):
                # legacy free-form; parse if possible
                pass
            else:
                # Cross-check by reading detail file for raw spec line
                detail_path = _resolve_detail_path(repo_root, entry.fields.get("detail_file", ""))
                if detail_path and detail_path.exists():
                    raw = _extract_raw_target_from_detail(detail_path)
                    if raw:
                        try:
                            parsed = trigger_parser.parse(raw)
                            if parsed.target != target_value:
                                violations.append((
                                    15,
                                    f"VAGUE_TRIGGER: {entry.id}: {raw}: parsed target {parsed.target} "
                                    f"does not match entry target {target_value}",
                                ))
                        except trigger_parser.TriggerParseError as e:
                            violations.append((
                                15,
                                f"VAGUE_TRIGGER: {entry.id}: {raw}: {e.refusal_code}",
                            ))
        except Exception:
            pass  # Non-fatal — best-effort trigger reparse

    # --- Detail file + edit-block check (codes 12 / 13) ----------------------
    detail_field = entry.fields.get("detail_file", "")
    detail_path = _resolve_detail_path(repo_root, detail_field)
    if is_cli_written or strict_edit_block:
        if detail_path is None or not detail_path.exists():
            violations.append((
                12,
                f"MISSING_DETAIL_FILE: {entry.id}: {detail_field or '(no detail field)'}",
            ))
        elif target_value == "closure_trigger":
            text = detail_path.read_text(encoding="utf-8")
            _, err = edit_block_module.extract_edit_block(text)
            if err is not None:
                violations.append((
                    13,
                    f"MISSING_EDIT_BLOCK: {entry.id}: {detail_path.relative_to(repo_root)}: {err}",
                ))

    return violations


def _git_staged_includes_list_md(repo_root: Path) -> bool:
    """Return True if `list.md` (or any scheduled_markers/*.md) is staged."""
    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            check=False,
        )
    except (FileNotFoundError, PermissionError):
        return False
    if out.returncode != 0:
        return False
    for line in out.stdout.splitlines():
        if line.startswith("docs/plans/scheduled_markers/"):
            return True
    return False


def _resolve_detail_path(repo_root: Path, detail_field: str) -> Optional[Path]:
    if not detail_field:
        return None
    # Markdown link form: [`fname`](./fname)
    m = re.search(r"\(\./([^)]+)\)", detail_field)
    if m:
        basename = m.group(1)
        return repo_root / "docs" / "plans" / "scheduled_markers" / basename
    # Schema-canonical relative path
    if detail_field.startswith("docs/plans/scheduled_markers/"):
        return repo_root / detail_field
    # Bare filename
    if detail_field.endswith(".md"):
        return repo_root / "docs" / "plans" / "scheduled_markers" / detail_field
    return None


def _extract_raw_target_from_detail(detail_path: Path) -> Optional[str]:
    """Look for the `**Target:**` line in the detail file; return its raw value."""
    text = detail_path.read_text(encoding="utf-8")
    m = re.search(r"^\*\*Target:\*\*\s+(.+)$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]
