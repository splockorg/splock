"""PostToolUse hook: validate + re-render a plan after a draft edit.

When a draft `<slug>_plan.json` is edited (allowed during the drafting phase
per the sealed_paths_hook phase gate), this hook runs `bin/render_plan
<slug> --kind plan`, which validates the JSON against `plan_v1.schema.json`
and refreshes the `<slug>_plan.md` twin. This keeps "edit the JSON, the MD
stays in sync and stays schema-valid" automatic instead of a manual
discipline.

Contract (mirrors `test_at_edit.py`):
  - PostToolUse → always exits 0 (PostToolUse cannot block).
  - Acts ONLY on the canonical `docs/plans/<slug>/<slug>_plan.json`; every
    other path (incl. `*_plan.md`, state files, non-plan files) is a silent
    skip.
  - render writes the MD via Python I/O (not the Edit/Write tool), so it does
    NOT re-trigger this hook — no loop.
  - On render/validation failure, emits a PostToolUse `additionalContext`
    advisory so the editor learns the JSON is now invalid and the MD was
    NOT refreshed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence


_RENDER_TIMEOUT_SECONDS = 30


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _plan_json_slug(file_path: str, repo_root: Path) -> str | None:
    """Return the slug iff `file_path` is the canonical draft plan JSON.

    Matches ONLY `docs/plans/<slug>/<slug>_plan.json` (relative or absolute).
    Returns None for `*_plan.md`, `_planner_call1_plan.md`, state files,
    nested paths, or anything outside `docs/plans/<slug>/`.
    """
    src = Path(file_path)
    if not src.is_absolute():
        src = repo_root / src
    try:
        rel = src.resolve().relative_to(repo_root.resolve())
    except (ValueError, OSError):
        return None
    parts = rel.parts
    if len(parts) != 4 or parts[0] != "docs" or parts[1] != "plans":
        return None
    slug = parts[2]
    if parts[3] != f"{slug}_plan.json":
        return None
    return slug


def process_event(
    event: dict,
    *,
    repo_root: Path | None = None,
    render_command: list[str] | None = None,
    timeout: int = _RENDER_TIMEOUT_SECONDS,
) -> dict:
    """Process one PostToolUse stdin event; return a result dict.

    `render_command` is injectable for tests (defaults to the real renderer).
    """
    root = repo_root or _repo_root()
    tool_input = event.get("tool_input", {}) if isinstance(event, dict) else {}
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not file_path:
        return {"action": "skipped", "reason": "no_file_path"}

    slug = _plan_json_slug(file_path, root)
    if slug is None:
        return {"action": "skipped", "reason": "not_plan_json", "path": file_path}

    base_cmd = render_command or ["python", "-m", "bin._render_plan.main"]
    cmd = [*base_cmd, slug, "--kind", "plan"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"action": "render_error", "slug": slug, "detail": str(exc)}

    if proc.returncode != 0:
        return {
            "action": "render_failed",
            "slug": slug,
            "rc": proc.returncode,
            "stderr": (proc.stderr or proc.stdout or "")[-800:],
        }
    return {"action": "rendered", "slug": slug}


def _emit_advisory(result: dict) -> None:
    """Emit a PostToolUse additionalContext advisory on render failure."""
    action = result.get("action")
    if action not in ("render_failed", "render_error"):
        return
    slug = result.get("slug", "<slug>")
    detail = result.get("stderr") or result.get("detail") or ""
    msg = (
        f"plan-render-on-edit: {slug}_plan.json did NOT pass validation/render "
        f"after your edit, so {slug}_plan.md was NOT refreshed. Fix the JSON, "
        f"then re-edit (or run `bin/render_plan {slug}`).\n{detail}"
    )
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": msg,
                }
            }
        )
        + "\n"
    )
    sys.stdout.flush()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="plan-render-on-edit")
    parser.add_argument("--repo-root", default=None, help="Override repo root (tests).")
    parser.add_argument("--event-file", default=None, help="Read event JSON from file.")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root) if args.repo_root else _repo_root()

    if args.event_file:
        raw = Path(args.event_file).read_text(encoding="utf-8")
    else:
        try:
            raw = sys.stdin.read()
        except (OSError, ValueError):
            raw = ""
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        event = {}

    try:
        result = process_event(event, repo_root=repo_root)
        _emit_advisory(result)
    except Exception:  # noqa: BLE001 — never block on a hook crash
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
