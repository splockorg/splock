"""Closed-set escalation trigger detectors (implplan §L.impl.4).

Five triggers from plan §L.3, each detected by CLI/hook (NOT by agent
narrative judgment). Trigger evaluation runs BEFORE the four-way rubric
per plan §L.2 "Critical ordering discipline".

Trigger inventory:

  1. blast_radius        — git diff --cached file count > ESCALATION_BLAST_RADIUS_FILES (15 default)
  2. ddl_multi           — multi-column DDL in scope (reads safe-ddl hook log)
  3. cross_vertical      — staged files span >1 vertical (process_graph.yaml)
  4. cross_repo          — any file outside repo root (abs path / symlink escape)
  5. operator_override_state — pending _state.json transition requires override

Trigger #4 cap_exhaustion from the plan is COVERED by §A.5 + §F.9.4 (chain
driver), NOT re-implemented here per L.impl.4 line 5885.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from pathlib import Path
from typing import List, Literal, Optional

from . import process_graph_resolver


DEFAULT_BLAST_RADIUS = 15
ENV_BLAST_RADIUS = "ESCALATION_BLAST_RADIUS_FILES"


TriggerKind = Literal[
    "blast_radius",
    "ddl_multi",
    "cross_vertical",
    "cross_repo",
    "operator_override_state",
    "none",
]


@dataclasses.dataclass(frozen=True)
class TriggerResult:
    """Outcome of `triggers.evaluate(...)`.

    `forced=True` → main.py short-circuits with exit 25 + refusal stderr.
    `forced=False` → main.py proceeds to rubric.route_after_triggers(...).
    """
    forced: bool
    trigger: TriggerKind
    detail: str
    staged_files: List[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class RouteContext:
    """Inputs to trigger evaluation. The CLI's argparse layer constructs one.

    Fields:
      repo_root           : repo root path
      staged_files        : override (else read git diff --cached --name-only)
      include_ddl_count   : enables --include-ddl-count detector
      check_override_state: enables --check-override-state detector
      allow_multi_ddl     : operator override for the DDL trigger
      ddl_log_path        : safe-ddl hook log fixture path (testing)
      override_state_path : _state.json override fixture path (testing)
      slug                : plan slug (operator-supplied per L.impl.12 #4)
    """
    repo_root: Path
    staged_files: Optional[List[str]] = None
    include_ddl_count: bool = False
    check_override_state: bool = False
    allow_multi_ddl: bool = False
    ddl_log_path: Optional[Path] = None
    override_state_path: Optional[Path] = None
    slug: Optional[str] = None


def blast_radius_threshold(env: Optional[dict] = None) -> int:
    """Read `ESCALATION_BLAST_RADIUS_FILES`; default 15 per §L.impl.11."""
    env = env if env is not None else os.environ
    val = env.get(ENV_BLAST_RADIUS)
    if val is None:
        return DEFAULT_BLAST_RADIUS
    try:
        n = int(val)
        if n <= 0:
            return DEFAULT_BLAST_RADIUS
        return n
    except ValueError:
        return DEFAULT_BLAST_RADIUS


def read_staged_files(repo_root: Path) -> List[str]:
    """Read `git diff --cached --name-only` from the given repo.

    Returns [] on failure (non-repo tmpdir, git not installed, etc.).
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    return [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]


def count_ddl_alterations(log_path: Optional[Path]) -> int:
    """Count multi-column DDL invocations from a safe-ddl hook log.

    Each line in the log is expected to be a JSON row with `alter_columns:
    int`. Sums the column count across all rows in the file. Returns 0 on
    missing / unreadable file.
    """
    if log_path is None or not log_path.exists():
        return 0
    total = 0
    try:
        for raw in log_path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                n = row.get("alter_columns")
                if isinstance(n, int):
                    total += n
    except OSError:
        return 0
    return total


def has_pending_override_state(state_path: Optional[Path]) -> bool:
    """True if a `_state.json` carries a pending-transition marker requiring
    OPERATOR_OVERRIDE_STATE=1 (per plan §1.E.1 matrix).

    Synthetic fixture shape: top-level key `pending_override_required: true`.
    Real `_state.json` carries this on transition attempts captured by the
    chain driver; the override check ensures the trigger only fires when
    the env-var is NOT set.
    """
    if state_path is None or not state_path.exists():
        return False
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    pending = data.get("pending_override_required")
    if pending is not True:
        return False
    # Trigger fires only when the env var is NOT set
    return os.environ.get("OPERATOR_OVERRIDE_STATE") != "1"


def evaluate(context: RouteContext, env: Optional[dict] = None) -> TriggerResult:
    """Evaluate all triggers in fixed order; short-circuit on first hit.

    Order: blast_radius → ddl_multi → cross_repo → cross_vertical → override_state.
    Cross-repo is checked BEFORE cross-vertical (higher severity).
    """
    env = env if env is not None else os.environ
    staged = context.staged_files
    if staged is None:
        staged = read_staged_files(context.repo_root)

    # 1. blast_radius
    threshold = blast_radius_threshold(env)
    if len(staged) > threshold:
        return TriggerResult(
            forced=True,
            trigger="blast_radius",
            detail=(
                f"blast_radius={len(staged)} files exceeds threshold "
                f"{threshold} (env {ENV_BLAST_RADIUS})"
            ),
            staged_files=staged,
        )

    # 2. ddl_multi
    if context.include_ddl_count and not context.allow_multi_ddl:
        ddl_n = count_ddl_alterations(context.ddl_log_path)
        if ddl_n > 1:
            return TriggerResult(
                forced=True,
                trigger="ddl_multi",
                detail=(
                    f"multi-column DDL detected: {ddl_n} columns altered "
                    f"in safe-ddl hook log; use --allow-multi-ddl to override"
                ),
                staged_files=staged,
            )

    # 3. cross_repo / cross_vertical
    forced_scope, kind, details = process_graph_resolver.check_scope(
        staged, context.repo_root
    )
    if forced_scope:
        detail_str = ", ".join(sorted(details))
        return TriggerResult(
            forced=True,
            trigger=kind,  # "cross_repo" or "cross_vertical"
            detail=f"{kind}: {detail_str}",
            staged_files=staged,
        )

    # 4. operator_override_state
    if context.check_override_state and has_pending_override_state(
        context.override_state_path
    ):
        return TriggerResult(
            forced=True,
            trigger="operator_override_state",
            detail=(
                "pending _state.json transition requires "
                "OPERATOR_OVERRIDE_STATE=1; emit structured handoff"
            ),
            staged_files=staged,
        )

    return TriggerResult(forced=False, trigger="none", detail="", staged_files=staged)
