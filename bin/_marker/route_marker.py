"""`bin/morning-review route-marker` wrapper (implplan §K.impl.9).

§H.impl will ship `bin/morning-review`; this module is the integration
seam — a thin wrapper that synthesizes `bin/marker create` args from a
morning-review entry context and dispatches via subprocess.

The wrapper:

1. Reads `(slug, task_id)` + operator args (--prefix, --reason, --detail).
2. Reads the morning-review entry at
   `docs/plans/<slug>/morning-review/<date>.md`, extracting:
     - deferral_reason (used to synthesize --trigger when not explicit)
     - verifier_reasoning, plan_section, module
3. Synthesizes `bin/marker create` args (or refuses if a trigger cannot
   be derived without an explicit operator --trigger).
4. Invokes `bin/marker create` via `subprocess.Popen` with
   `emitted_by="bin/morning-review:route-marker"`.
5. On exit 0, calls back into §H.impl's mutator (stubbed pending §H.impl).
6. Emits a `morning_review_task_routed` row to _orchestrator_log.jsonl
   (the underlying `marker_created` row is emitted by `bin/marker create`,
   so we don't double-emit).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import log_emit


def run(
    *,
    slug: str,
    task_id: str,
    new_prefix: str,
    reason: str,
    detail: Optional[str] = None,
    explicit_trigger: Optional[str] = None,
    repo_root: Optional[Path] = None,
    json_output: bool = False,
) -> int:
    repo_root = repo_root or _repo_root()

    # --- Step 1: read morning-review entry ----------------------------------
    mr_context = _read_morning_review_context(repo_root, slug, task_id)
    if mr_context is None:
        msg = f"morning-review entry not found for slug={slug}, task_id={task_id}"
        if json_output:
            print(json.dumps({"error": "mr-entry-not-found", "message": msg}))
        else:
            print(msg, file=sys.stderr)
        return 2

    # --- Step 2: synthesize args ---------------------------------------------
    trigger = explicit_trigger
    if trigger is None:
        trigger = _synthesize_trigger(mr_context)
    if trigger is None:
        msg = (
            "trigger could not be synthesized; pass explicit --trigger "
            "via route-marker."
        )
        if json_output:
            print(json.dumps({"error": "trigger-synthesis-failed", "message": msg}))
        else:
            print(msg, file=sys.stderr)
        return 3

    title = mr_context.get("task_title", f"Routed from morning-review task {task_id}")
    title = title[:200]  # cap at 200 chars per schema

    data_needed = (reason or "") + (
        f" {mr_context.get('verifier_reasoning', '')}"
        if mr_context.get("verifier_reasoning")
        else ""
    )
    data_needed = data_needed.strip() or "(operator routed; see context)"

    context = (
        f"Routed from morning-review on {mr_context.get('date', '<date>')}: "
        f"{mr_context.get('verifier_reasoning', '')} {reason or ''}"
    ).strip()

    module = mr_context.get("module") or "cross-cutting"

    # --- Step 3: invoke `bin/marker create` via subprocess ------------------
    cmd = [
        str(repo_root / "bin" / "marker"),
        "create", new_prefix, title,
        "--trigger", trigger,
        "--plan", slug,
        "--module", module,
        "--data-needed", data_needed,
        "--context", context,
        "--emitted-by", "bin/morning-review:route-marker",
    ]
    if detail:
        cmd.extend(["--detail", detail])
    if json_output:
        cmd.append("--json")

    proc = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        # Propagate refusal verbatim
        return proc.returncode

    # --- Step 4 + 5: §H.impl call-back + log row ---------------------------
    # §H mutator stubbed — call site will be wired by §H.impl
    log_emit._append(
        _resolve_plan_dir(repo_root, slug),
        {
            "transition.from": "wip",
            "transition.to": "deferred",
            "reason": (
                f"morning_review_task_routed task_id={task_id} → marker route"
            ),
            "task_id": task_id,
            "session_id": log_emit._session_id(),
            "plan_slug": slug,
            "mode_at_transition.overnight": False,
            "mode_at_transition.guardrail": True,
        },
        log_emit.EMIT_ROUTE_MARKER,
    )
    return 0


def _resolve_plan_dir(repo_root: Path, slug: str) -> Path:
    return repo_root / "docs" / "plans" / slug


def _read_morning_review_context(repo_root: Path, slug: str, task_id: str) -> Optional[dict]:
    """Parse a morning-review entry. Returns None if not found.

    §H.impl owns the canonical morning-review schema. We extract a minimal
    set of fields the wrapper needs to synthesize a create call.
    """
    mr_dir = repo_root / "docs" / "plans" / slug / "morning-review"
    if not mr_dir.exists() or not mr_dir.is_dir():
        return None
    # Find any morning-review file containing this task_id; prefer newest
    files = sorted(mr_dir.glob("*.md"), reverse=True)
    for f in files:
        text = f.read_text(encoding="utf-8")
        if f"task_id: {task_id}" in text or f"### {task_id}" in text:
            return _extract_mr_fields(text, task_id, f.stem)
    return None


def _extract_mr_fields(text: str, task_id: str, date_stem: str) -> dict:
    """Best-effort field extraction. §H.impl will refine."""
    ctx = {"task_id": task_id, "date": date_stem}
    # title
    m = re.search(rf"###\s+{re.escape(task_id)}\s+—\s+(.+)$", text, re.MULTILINE)
    if m:
        ctx["task_title"] = m.group(1).strip()
    # deferral_reason
    m = re.search(r"deferral_reason:\s*(.+)$", text, re.MULTILINE)
    if m:
        ctx["deferral_reason"] = m.group(1).strip()
    # verifier_reasoning
    m = re.search(r"verifier_reasoning:\s*(.+)$", text, re.MULTILINE)
    if m:
        ctx["verifier_reasoning"] = m.group(1).strip()
    # module
    m = re.search(r"module:\s*(.+)$", text, re.MULTILINE)
    if m:
        ctx["module"] = m.group(1).strip()
    # plan_section
    m = re.search(r"plan_section:\s*(.+)$", text, re.MULTILINE)
    if m:
        ctx["plan_section"] = m.group(1).strip()
    return ctx


def _synthesize_trigger(mr_context: dict) -> Optional[str]:
    """Try to infer a structured trigger from `deferral_reason`."""
    reason = mr_context.get("deferral_reason", "").strip()
    if not reason:
        return None

    # File path → edit:<path>:any
    path_match = re.match(r"^([a-zA-Z0-9_/.\-]+\.(py|md|yaml|yml|json|sh|sql))\b", reason)
    if path_match:
        return f"edit:{path_match.group(1)}:any"

    # ISO date
    date_match = re.match(r"^(\d{4}-\d{2}-\d{2})$", reason)
    if date_match:
        return f"date:{date_match.group(1)}"

    # condition: prefix
    if reason.startswith("condition:") or reason.startswith("exists:") or reason.startswith("SELECT "):
        return f"condition:{reason}" if not reason.startswith("condition:") else reason

    return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]
