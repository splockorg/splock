"""`_chain_sessions.json` reader (flock-aware) — hook-side classifier.

Per implplan §G.impl.4 + §G.impl.6 hook decision logic. Hooks need to
know "is the chain currently in phase 5 / in_progress" before doing
work. This is a read-only consumer; the driver (§A.impl) and
`splock-session-start.sh` (§G.impl.3) are the writers.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ManifestPhaseStatus:
    """Outcome of `latest_phase_status(...)`.

    `present == False` means no manifest file exists or it has no
    phases array. Callers that need "is the chain in test-step retry
    loop" check `present AND phase == 5 AND result == "in_progress"`.
    """
    present: bool
    phase: int | None = None
    result: str | None = None
    session_id: str | None = None
    chain_id: str | None = None


def manifest_path(plan_dir: Path) -> Path:
    return plan_dir / "_chain_sessions.json"


def latest_phase_status(plan_dir: Path) -> ManifestPhaseStatus:
    """Read the latest entry from `_chain_sessions.json`.

    Lock-free read — the writer side uses flock + atomic-rename, so a
    stale read at worst returns the previous-state-of-the-world (which
    is correct for the hook's purpose: if no in-progress phase 5 row,
    the hook no-ops).
    """
    path = manifest_path(plan_dir)
    if not path.exists():
        return ManifestPhaseStatus(present=False)
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except (OSError, json.JSONDecodeError):
        return ManifestPhaseStatus(present=False)
    phases = data.get("phases") if isinstance(data, dict) else None
    if not isinstance(phases, list) or not phases:
        return ManifestPhaseStatus(present=False)
    last = phases[-1]
    if not isinstance(last, dict):
        return ManifestPhaseStatus(present=False)
    return ManifestPhaseStatus(
        present=True,
        phase=last.get("phase") if isinstance(last.get("phase"), int) else None,
        result=last.get("result") if isinstance(last.get("result"), str) else None,
        session_id=last.get("session_id") if isinstance(last.get("session_id"), str) else None,
        chain_id=last.get("chain_id") if isinstance(last.get("chain_id"), str) else None,
    )


def in_test_step_window(plan_dir: Path) -> bool:
    """True iff the latest manifest phase is 5 AND result is in_progress.

    Convenience predicate for chain-suppression-block / chain-test-file-
    edit-flag activation guards.
    """
    status = latest_phase_status(plan_dir)
    if not status.present:
        return False
    return status.phase == 5 and status.result == "in_progress"


def env_slug_to_plan_dir() -> Path | None:
    """Resolve `SPLOCK_PLAN_SLUG` env var to `docs/plans/<slug>/` Path.

    Returns None if env var is unset/empty.
    """
    slug = os.environ.get("SPLOCK_PLAN_SLUG", "").strip()
    if not slug:
        return None
    return Path("docs") / "plans" / slug


__all__ = [
    "ManifestPhaseStatus",
    "manifest_path",
    "latest_phase_status",
    "in_test_step_window",
    "env_slug_to_plan_dir",
]
