"""Operator-runnable retention purge for `_failures/<id>.json`.

Per splock implplan §J.impl.5 (retention policy: 30 days for
unpromoted failures; promoted failures purgeable immediately).
`EVAL_FAILURE_RETENTION_DAYS` env var (default 30) per §J.impl.15 #2.

Operator runs: `python -m bin._eval_common.failure_gc <slug>` — not auto-run.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
from typing import Optional


DEFAULT_RETENTION_DAYS = 30


def _retention_days() -> int:
    raw = os.environ.get("EVAL_FAILURE_RETENTION_DAYS")
    if raw is None or raw == "":
        return DEFAULT_RETENTION_DAYS
    try:
        val = int(raw)
    except ValueError:
        return DEFAULT_RETENTION_DAYS
    if val < 7 or val > 365:
        return DEFAULT_RETENTION_DAYS
    return val


def _parse_iso_z(ts: str) -> datetime.datetime:
    # Accept both microsecond and second resolution; strip trailing Z.
    ts2 = ts.rstrip("Z")
    return datetime.datetime.fromisoformat(ts2).replace(tzinfo=datetime.timezone.utc)


def purge_unpromoted(
    plan_dir: pathlib.Path,
    retention_days: Optional[int] = None,
    *,
    now: Optional[datetime.datetime] = None,
    dry_run: bool = False,
) -> list[str]:
    """Remove `_failures/<id>.json` where `promoted_to_regression_case` is
    null AND `captured_at < now - retention_days`. Returns the list of
    failure_ids purged (or that would be purged when dry_run=True).
    """
    if retention_days is None:
        retention_days = _retention_days()
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=retention_days)
    fdir = plan_dir / "_failures"
    if not fdir.exists():
        return []
    purged: list[str] = []
    for path in sorted(fdir.glob("failure_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("promoted_to_regression_case") is not None:
            continue  # promoted; preserved
        captured = payload.get("captured_at")
        if not isinstance(captured, str):
            continue
        try:
            captured_dt = _parse_iso_z(captured)
        except ValueError:
            continue
        if captured_dt < cutoff:
            purged.append(payload.get("failure_id", path.stem))
            if not dry_run:
                try:
                    path.unlink()
                except OSError:
                    pass
    return purged


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="failure_gc")
    p.add_argument("slug")
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    p.add_argument("--retention-days", type=int, default=None)
    p.add_argument("--json", action="store_true", dest="json_output")
    args = p.parse_args(argv)

    plan_dir = _repo_root() / "docs" / "plans" / args.slug
    purged = purge_unpromoted(
        plan_dir,
        retention_days=args.retention_days,
        dry_run=args.dry_run,
    )
    if args.json_output:
        print(json.dumps({"purged": purged, "dry_run": args.dry_run}))
    else:
        if not purged:
            print("(nothing to purge)")
        else:
            for fid in purged:
                prefix = "[dry-run] " if args.dry_run else ""
                print(f"{prefix}{fid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["purge_unpromoted", "DEFAULT_RETENTION_DAYS", "main"]
