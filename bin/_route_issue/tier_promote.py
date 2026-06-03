"""`--type tier-promote` handler (implplan §L.impl.8).

Promotion-preserves-history mechanics per plan §L.8:

  1. Read origin line via --context <line_id>; refuse exit 2 if not found
  2. Verify origin status: open; refuse exit 27 if promoted/resolved/superseded
  3. mkdir -p docs/plans/<new-slug>/; refuse exit 27 if exists
  4. Write skeleton _recon.md from template
  5. Mutate origin: status: open → status: promoted; add promoted_to: <slug>
  6. Emit outstanding_promoted row via §C writer

Slug validation: `^[a-z0-9_]+$` (operator-supplied per L.impl.12 #4 RATIFIED).
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import line_format, log_emit, outstanding
from .exit_codes import (
    EXIT_ATOMIC_WRITE_FAILED,
    EXIT_OK,
    EXIT_ORIGIN_LINE_NOT_FOUND,
    EXIT_TIER_PROMOTE_SLUG_EXISTS,
)


SLUG_RE = re.compile(r"^[a-z0-9_]+$")

TEMPLATE_REL = ".claude/templates/tier_promote_recon.md.template"


def _recon_template_path(repo_root: Path) -> Path:
    return repo_root / TEMPLATE_REL


def _outstanding_path(repo_root: Path) -> Path:
    return repo_root / "docs" / "outstanding_issues.md"


def _validate_slug(slug: str) -> Optional[str]:
    if not SLUG_RE.match(slug):
        return (
            f"slug={slug!r} does not match `^[a-z0-9_]+$` "
            f"(kebab + underscore lowercase only)"
        )
    return None


def _read_template(repo_root: Path) -> str:
    p = _recon_template_path(repo_root)
    if not p.exists():
        # Inline fallback so tests + ship paths don't both require the template.
        return _INLINE_TEMPLATE
    return p.read_text(encoding="utf-8")


_INLINE_TEMPLATE = """# <new-slug> — recon

> **Promoted from `docs/outstanding_issues.md`** line ID `<line_id>`
> on `<promotion_timestamp>`. Originating plan: `<original_plan_slug>`.
> Original deferral timestamp: `<original_timestamp>`.
> Original deferral reason: `<original_deferral_reason>`.

## Problem statement

(populate during recon pass; reference origin line above for forensic
chain.)

## Scope

## Open questions

## References
- Origin: `docs/outstanding_issues.md` — line ID `<line_id>`, status `promoted`
- (additional references discovered during recon)
"""


def _render_template(template: str, *, new_slug: str, line_id: str,
                     promotion_ts: str, original_plan_slug: str,
                     original_ts: str, original_gloss: str) -> str:
    """Substitute template variables. Plain string replace (no Jinja)."""
    out = template
    out = out.replace("<new-slug>", new_slug)
    out = out.replace("<line_id>", line_id)
    out = out.replace("<promotion_timestamp>", promotion_ts)
    out = out.replace("<original_plan_slug>", original_plan_slug)
    out = out.replace("<original_timestamp>", original_ts)
    out = out.replace("<original_deferral_reason>", original_gloss)
    return out


def _rewrite_outstanding_with_promotion(
    text: str,
    line_id: str,
    new_slug: str,
) -> Optional[str]:
    """Rewrite outstanding_issues.md to mark the origin line as promoted.

    Finds the entry block containing `  - line_id: <id>`, mutates
    `  - status: open` → `  - status: promoted`, and inserts/updates
    `  - promoted_to: <new-slug>` immediately after the status line.

    Returns the new text, or None if the origin block was not found or
    its status was not `open`.
    """
    lines = text.splitlines(keepends=False)
    # Locate the top-line of the entry whose line_id matches
    # Strategy: scan for a SUB_LINE_RE match with key=line_id and value matching;
    # then walk upward to find the matching top-line.
    target_top_idx: Optional[int] = None
    target_status_idx: Optional[int] = None
    target_promoted_to_idx: Optional[int] = None
    target_block_end: Optional[int] = None

    for i, line in enumerate(lines):
        sm = line_format.SUB_LINE_RE.match(line)
        if sm and sm.group("key") == "line_id" and sm.group("val").strip() == line_id:
            # Walk upward to find the top-line
            j = i - 1
            while j >= 0:
                if line_format.TOP_LINE_RE.match(lines[j]):
                    target_top_idx = j
                    break
                # Stop on blank / non-sub-line
                if not lines[j].startswith("  -"):
                    break
                j -= 1
            if target_top_idx is None:
                continue
            # Walk forward from top-line to find status / promoted_to / end
            k = target_top_idx + 1
            while k < len(lines):
                sm2 = line_format.SUB_LINE_RE.match(lines[k])
                if not sm2:
                    target_block_end = k
                    break
                key = sm2.group("key")
                if key == "status":
                    target_status_idx = k
                elif key == "promoted_to":
                    target_promoted_to_idx = k
                k += 1
            if target_block_end is None:
                target_block_end = k
            break

    if target_top_idx is None or target_status_idx is None:
        return None

    status_line = lines[target_status_idx]
    sm = line_format.SUB_LINE_RE.match(status_line)
    if not sm or sm.group("val").strip() != "open":
        # Idempotent re-run refused per §L.impl.8 step 2
        return None

    # Mutate status line; insert promoted_to right after (or update existing)
    new_lines = list(lines)
    new_lines[target_status_idx] = "  - status: promoted"
    promoted_to_line = f"  - promoted_to: {new_slug}"
    if target_promoted_to_idx is not None:
        new_lines[target_promoted_to_idx] = promoted_to_line
    else:
        # Insert after status line
        new_lines.insert(target_status_idx + 1, promoted_to_line)

    # Preserve trailing newline if original had one
    text_out = "\n".join(new_lines)
    if text.endswith("\n"):
        text_out += "\n"
    return text_out


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


def _find_origin_entry(repo_root: Path, line_id: str) -> Optional[line_format.OutstandingEntry]:
    path = _outstanding_path(repo_root)
    entries = line_format.parse_outstanding_md(path)
    for e in entries:
        if e.line_id == line_id:
            return e
    return None


def run(
    *,
    slug: str,
    line_id: str,
    dry_run: bool = False,
    json_output: bool = False,
    repo_root: Path,
    now: Optional[datetime] = None,
) -> int:
    """Promote one outstanding entry to a new `docs/plans/<slug>/` recon.

    Returns:
      EXIT_OK on success.
      EXIT_USAGE / 1 on bad slug shape (handled by main.py argparse normally).
      EXIT_ORIGIN_LINE_NOT_FOUND (2) if line_id missing.
      EXIT_TIER_PROMOTE_SLUG_EXISTS (27) on slug-dir collision OR origin not open.
      EXIT_ATOMIC_WRITE_FAILED (7) on filesystem failure.
    """
    slug_err = _validate_slug(slug)
    if slug_err:
        msg = f"tier_promote_slug_invalid: {slug_err}"
        if json_output:
            print(json.dumps({"error": "usage", "message": msg}))
        else:
            print(msg, file=sys.stderr)
        return 1

    origin = _find_origin_entry(repo_root, line_id)
    if origin is None:
        msg = f"origin_line_not_found: line_id={line_id!r} absent from outstanding_issues.md"
        if json_output:
            print(json.dumps({"error": "origin_line_not_found", "line_id": line_id}))
        else:
            print(msg, file=sys.stderr)
        return EXIT_ORIGIN_LINE_NOT_FOUND

    if origin.status != "open":
        msg = (
            f"tier_promote_idempotent_refusal: origin line_id={line_id} has "
            f"status={origin.status!r} (must be 'open')"
        )
        if json_output:
            print(json.dumps({
                "error": "tier_promote_slug_exists",
                "reason": "origin_status_not_open",
                "current_status": origin.status,
            }))
        else:
            print(msg, file=sys.stderr)
        return EXIT_TIER_PROMOTE_SLUG_EXISTS

    target_dir = repo_root / "docs" / "plans" / slug
    if target_dir.exists():
        msg = f"tier_promote_slug_exists: docs/plans/{slug}/ already exists"
        if json_output:
            print(json.dumps({"error": "tier_promote_slug_exists", "slug": slug}))
        else:
            print(msg, file=sys.stderr)
        return EXIT_TIER_PROMOTE_SLUG_EXISTS

    when = now or datetime.now(timezone.utc)
    promotion_ts = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    template = _read_template(repo_root)
    rendered = _render_template(
        template,
        new_slug=slug,
        line_id=line_id,
        promotion_ts=promotion_ts,
        original_plan_slug=origin.plan_slug or "(unknown)",
        original_ts=origin.timestamp,
        original_gloss=origin.gloss,
    )

    if dry_run:
        if json_output:
            print(json.dumps({
                "result": "dry-run",
                "type": "tier-promote",
                "slug": slug,
                "line_id": line_id,
                "recon_preview": rendered[:200],
            }))
        else:
            print(f"[dry-run] would mkdir docs/plans/{slug}/")
            print(f"[dry-run] would write _recon.md ({len(rendered)} bytes)")
            print(f"[dry-run] would mutate origin {line_id}: status open → promoted")
        return EXIT_OK

    # Filesystem mutations: mkdir + recon + outstanding.md update (atomic+flock)
    try:
        target_dir.mkdir(parents=True, exist_ok=False)
        recon_path = target_dir / "_recon.md"
        _atomic_write(recon_path, rendered)
    except FileExistsError:
        # Race: directory got created between our exists check and mkdir
        return EXIT_TIER_PROMOTE_SLUG_EXISTS
    except OSError as e:
        msg = f"atomic_write_failed (recon): {e}"
        if json_output:
            print(json.dumps({"error": "atomic_write_failed", "message": str(e)}))
        else:
            print(msg, file=sys.stderr)
        return EXIT_ATOMIC_WRITE_FAILED

    outstanding_path = _outstanding_path(repo_root)
    try:
        with outstanding._flock_outstanding(outstanding_path):
            current = outstanding_path.read_text(encoding="utf-8")
            new_text = _rewrite_outstanding_with_promotion(current, line_id, slug)
            if new_text is None:
                # Origin disappeared between read and lock (or status changed).
                # Roll back the recon dir.
                try:
                    recon_path.unlink()
                    target_dir.rmdir()
                except OSError:
                    pass
                msg = (
                    f"origin_line_not_found: race condition — line_id={line_id} "
                    f"vanished between dry check and mutation"
                )
                if json_output:
                    print(json.dumps({"error": "origin_line_not_found"}))
                else:
                    print(msg, file=sys.stderr)
                return EXIT_ORIGIN_LINE_NOT_FOUND
            _atomic_write(outstanding_path, new_text)
    except OSError as e:
        msg = f"atomic_write_failed (outstanding): {e}"
        if json_output:
            print(json.dumps({"error": "atomic_write_failed", "message": str(e)}))
        else:
            print(msg, file=sys.stderr)
        return EXIT_ATOMIC_WRITE_FAILED

    # Emit log row
    plan_dir = log_emit.resolve_plan_dir(None, slug)
    log_emit.emit_row(
        plan_dir=plan_dir,
        plan_slug=slug,
        transition_from="deferred",
        transition_to="ready",
        reason=(
            f"outstanding_promoted: line_id={line_id} → docs/plans/{slug}/"
        ),
        emitted_by=log_emit.EMIT_TIER_PROMOTE,
        extra={
            "event_type": "outstanding_promoted",
            "from_line_id": line_id,
            "to_slug": slug,
        },
    )

    if json_output:
        print(json.dumps({
            "result": "promoted",
            "type": "tier-promote",
            "slug": slug,
            "line_id": line_id,
            "recon_path": f"docs/plans/{slug}/_recon.md",
        }))
    else:
        print(f"tier-promote: docs/plans/{slug}/_recon.md created")
        print(f"  origin line_id={line_id} → status: promoted")
    return EXIT_OK
