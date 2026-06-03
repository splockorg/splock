"""Context-dispatched collision resolution (implplan §P.impl.6).

Two modes:
  - interactive  → structured stderr payload; caller exits 40.
  - autonomous   → two-step pattern per §F.impl.7 halt-handoff precedent:
                   (1) subprocess `bin/morning-review --internal-bootstrap-day <date> --slug <slug>`
                   (2) direct atomic append of a `collision_detected` entry
                       to `docs/plans/<slug>/morning-review/<date>.md` via
                       `bin/_morning_review.entry_format.render_entry(...)`
                       under flock + atomic temp+rename.

Per the v1.4-parallel-revised-2 MAJOR-3 correction: bootstrap CLI does
NOT accept `--deferral-reason` or `--collision-id`; that data lives in
the entry body written by step 2.

HiL-Bench rationale (P.impl.6 lines 8705-8715): frontier agents
verbalize infeasibility but submit anyway. Routing collisions to the
morning-review deferral surface — same surface as retry-cap-exhaustion
and GUARDRAIL refusal — removes the agent's ability to pick
non-escalation.
"""

from __future__ import annotations

import datetime
import fcntl
import json
import logging
import os
import pathlib
import subprocess
import sys
import tempfile
from typing import Any, Optional

logger = logging.getLogger(__name__)


def is_autonomous_context(chain_id_arg: Optional[str]) -> bool:
    """Return True when collision should dispatch to morning-review.

    Per the P.impl.6 trigger table: `--chain-id` set OR `SPLOCK_CHAIN_ID`
    env populated → autonomous.
    """
    if chain_id_arg:
        return True
    return bool(os.environ.get("SPLOCK_CHAIN_ID"))


def handle_collision(
    *,
    mode: str,
    collision_id: str,
    colliding_session_id: str,
    colliding_area: str,
    lineage_snapshot: list[dict],
    plan_slug: Optional[str],
    chain_id: Optional[str],
    repo_root: Optional[pathlib.Path] = None,
    stderr=None,
) -> dict:
    """Resolve a collision per mode. Returns a result-summary dict for
    the caller to surface in --json output."""
    if mode == "interactive":
        return _handle_interactive(
            collision_id=collision_id,
            colliding_session_id=colliding_session_id,
            colliding_area=colliding_area,
            lineage_snapshot=lineage_snapshot,
            stderr=stderr,
        )
    return _handle_autonomous(
        collision_id=collision_id,
        colliding_session_id=colliding_session_id,
        colliding_area=colliding_area,
        lineage_snapshot=lineage_snapshot,
        plan_slug=plan_slug,
        chain_id=chain_id,
        repo_root=repo_root,
    )


def _handle_interactive(
    *,
    collision_id: str,
    colliding_session_id: str,
    colliding_area: str,
    lineage_snapshot: list[dict],
    stderr,
) -> dict:
    """Interactive mode: emit structured stderr; operator resolves via
    `bin/intent pivot` or `bin/intent register --area <new>`."""
    payload = {
        "error": "intent_collision_detected",
        "collision_id": collision_id,
        "colliding_session_id": colliding_session_id,
        "colliding_area": colliding_area,
        "lineage_snapshot": lineage_snapshot,
        "resolve": (
            f"bin/intent pivot {colliding_session_id} --area <new_ID>  "
            f"OR  bin/intent register --area <new_ID> ..."
        ),
        "plan_citation": "§P.impl.6 interactive dispatch",
    }
    (stderr or sys.stderr).write(
        json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n"
    )
    return {
        "dispatch_mode": "interactive",
        "collision_id": collision_id,
        "resolution_pending": True,
    }


def _handle_autonomous(
    *,
    collision_id: str,
    colliding_session_id: str,
    colliding_area: str,
    lineage_snapshot: list[dict],
    plan_slug: Optional[str],
    chain_id: Optional[str],
    repo_root: Optional[pathlib.Path],
) -> dict:
    """Autonomous mode: bootstrap daily file + atomic-append collision entry."""
    root = repo_root or _repo_root()
    today = _today_yyyy_mm_dd()
    slug = plan_slug or os.environ.get("SPLOCK_PLAN_SLUG") or "scheduled_markers"
    daily_dir = root / "docs" / "plans" / slug / "morning-review"
    daily_file = daily_dir / f"{today}.md"

    _bootstrap_day(repo_root=root, today=today, slug=slug, daily_file=daily_file)

    # Build the entry body via §H render_entry. We synthesize a deferred
    # entry with deferral_reason='collision_detected ...' + collision_id +
    # lineage_snapshot JSON blob.
    task_id = f"T{int(datetime.datetime.now(datetime.timezone.utc).timestamp()) % 100000}"
    status_since = _now_hms_z()
    deferral_reason = (
        f"collision_detected colliding_session_id={colliding_session_id} "
        f"area={colliding_area}"
    )
    lineage_json = json.dumps(lineage_snapshot, sort_keys=True, ensure_ascii=False)

    try:
        from bin._morning_review.entry_format import render_entry
    except ImportError as exc:  # pragma: no cover — §H always shipped before §P
        logger.warning("morning_review entry_format import failed: %s", exc)
        return {
            "dispatch_mode": "autonomous",
            "collision_id": collision_id,
            "morning_review_write_failed": True,
            "reason": str(exc),
        }

    body = render_entry(
        task_id=task_id,
        status_since=status_since,
        slug=slug,
        chain_id=chain_id or os.environ.get("SPLOCK_CHAIN_ID") or "(unset)",
        phase=int(os.environ.get("SPLOCK_PHASE", "0") or "0"),
        deferral_reason=deferral_reason,
        retry_count=0,
        verifier_verdict_ref="(none — intent collision, not verifier deferral)",
        verifier_reasoning=(
            f"Intent collision: session {colliding_session_id} attempted to "
            f"claim area {colliding_area!r} but it overlaps an active session. "
            f"Operator must pivot or proceed via override."
        ),
        collision_id=collision_id,
        lineage_snapshot=lineage_json,
    )

    _append_atomic(daily_file, body)
    return {
        "dispatch_mode": "autonomous",
        "collision_id": collision_id,
        "morning_review_entry": str(daily_file),
        "task_id": task_id,
    }


def _bootstrap_day(
    *, repo_root: pathlib.Path, today: str, slug: str, daily_file: pathlib.Path
) -> None:
    """Subprocess call to `bin/morning-review --internal-bootstrap-day <date>
    --slug <slug>`. Best-effort — if the CLI is unavailable or fails,
    write a minimal shell so the autonomous append still lands."""
    daily_file.parent.mkdir(parents=True, exist_ok=True)
    if daily_file.exists():
        return
    bin_path = repo_root / "bin" / "morning-review"
    if bin_path.exists():
        try:
            subprocess.run(
                [
                    str(bin_path),
                    "--internal-bootstrap-day",
                    today,
                    "--slug",
                    slug,
                ],
                cwd=str(repo_root),
                capture_output=True,
                timeout=30,
                check=False,
            )
            if daily_file.exists():
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
    # Fallback shell — same posture as bin/_retry_loop/halt_handoff.py.
    daily_file.write_text(
        f"# Morning review queue — {slug} — {today}\n"
        f"\n"
        f"_Bootstrap fallback (bin/morning-review unavailable from §P "
        f"autonomous-mode dispatch)._\n",
        encoding="utf-8",
    )


def _append_atomic(target: pathlib.Path, body: str) -> None:
    """Atomic temp+rename under flock on `<file>.lock`.

    Mirrors `bin/_retry_loop/halt_handoff.py::_append_atomic` discipline.
    """
    lock_path = target.with_suffix(target.suffix + ".lock")
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        new_text = existing.rstrip("\n") + "\n\n" + body
        with tempfile.NamedTemporaryFile(
            dir=str(target.parent),
            prefix="." + target.name + ".",
            suffix=".tmp",
            delete=False,
            mode="w",
            encoding="utf-8",
        ) as fh:
            fh.write(new_text)
            fh.flush()
            os.fsync(fh.fileno())
            tmp_name = fh.name
        os.replace(tmp_name, str(target))
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _today_yyyy_mm_dd() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _now_hms_z() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%SZ")


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


__all__ = [
    "is_autonomous_context",
    "handle_collision",
]
