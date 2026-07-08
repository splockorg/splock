"""Telemetry queries for `bin/develop-plan-bypass-status` (implplan §E.impl.6).

Read-only over existing log substrate (`_orchestrator_log.jsonl` files
under `docs/plans/<slug>/`) and `_state.json` files. No durable cache;
recomputed on every invocation. Single-pass scan.

Telemetry surface (per plan §E.6):

| Field | Source | Semantics |
|---|---|---|
| `qualifying_run_count` | sessions in `_orchestrator_log.jsonl` | sessions with at least one row stamped `emitted_by="bin/update_orchestrator --from-develop-plan"` AND no `_corrupt_truncated` audit rows AND no override-state rows |
| `revision_path_exercised` | iteration_history across all task entries | true iff any qualifying session's task has at least one `outcome: "needs_revision"` |
| `manual_correction_count` | sessions w/ `emitted_by="bin/update_orchestrator"` (bare) + override_in_effect.operator_override_state=true | falls within a `--from-develop-plan` session's wall-clock window |
| `calendar_days_since_bypass_started` | earliest `bin/chain-overnight` emit | `(now - earliest_ts) / 86400` |
| `bypass_status` | derived | closed enum: `interim` / `eligible_to_exit` / `escalate_backstop` |
"""

from __future__ import annotations

import dataclasses
import datetime
import json
from pathlib import Path
from typing import Iterable, Iterator, Optional

from bin._env_paths import project_root

QUALIFYING_EMITTER = "bin/update_orchestrator --from-develop-plan"
BASE_EMITTER = "bin/update_orchestrator"
CHAIN_DRIVER_EMITTER = "bin/chain-overnight"

# Bypass-exit thresholds (per plan §E.6 line 1579)
ELIGIBLE_QUALIFYING_RUNS = 3
ESCALATE_DAYS = 42  # 6 weeks per plan §1.H

STATUS_INTERIM = "interim"
STATUS_ELIGIBLE_TO_EXIT = "eligible_to_exit"
STATUS_ESCALATE_BACKSTOP = "escalate_backstop"


@dataclasses.dataclass
class BypassReport:
    qualifying_run_count: int
    revision_path_exercised: bool
    manual_correction_count: int
    calendar_days_since_bypass_started: Optional[float]
    bypass_status: str
    qualifying_sessions: list[str] = dataclasses.field(default_factory=list)
    manual_correction_sessions: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "qualifying_run_count": self.qualifying_run_count,
            "revision_path_exercised": self.revision_path_exercised,
            "manual_correction_count": self.manual_correction_count,
            "calendar_days_since_bypass_started": self.calendar_days_since_bypass_started,
            "bypass_status": self.bypass_status,
            "qualifying_sessions": list(self.qualifying_sessions),
            "manual_correction_sessions": list(self.manual_correction_sessions),
        }


def _iter_jsonl_rows(path: Path) -> Iterator[dict]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # Corrupt line; skip (recovery layer handles truncation upstream)
                continue
            if isinstance(row, dict):
                yield row


def _parse_iso_ts(ts: str) -> Optional[datetime.datetime]:
    if not isinstance(ts, str):
        return None
    try:
        if ts.endswith("Z"):
            return datetime.datetime.fromisoformat(ts[:-1]).replace(
                tzinfo=datetime.timezone.utc
            )
        return datetime.datetime.fromisoformat(ts)
    except ValueError:
        return None


def _gather_log_rows(plan_dirs: Iterable[Path]) -> list[dict]:
    rows: list[dict] = []
    for d in plan_dirs:
        log_path = d / "_orchestrator_log.jsonl"
        rows.extend(_iter_jsonl_rows(log_path))
    return rows


def _gather_state_files(plan_dirs: Iterable[Path]) -> list[dict]:
    states: list[dict] = []
    for d in plan_dirs:
        state_path = d / "_state.json"
        if not state_path.is_file():
            continue
        try:
            states.append(json.loads(state_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return states


def _has_revision_path(states: Iterable[dict]) -> bool:
    for state in states:
        tasks = state.get("tasks") or {}
        if not isinstance(tasks, dict):
            continue
        for entry in tasks.values():
            if not isinstance(entry, dict):
                continue
            tel = entry.get("develop_plan_telemetry")
            if not isinstance(tel, dict):
                continue
            history = tel.get("iteration_history") or []
            for row in history:
                if isinstance(row, dict) and row.get("outcome") == "needs_revision":
                    return True
    return False


def compute_bypass_report(
    plan_dirs: Iterable[Path],
    *,
    now: Optional[datetime.datetime] = None,
    since: Optional[datetime.datetime] = None,
) -> BypassReport:
    """Compute the bypass-exit telemetry report.

    Parameters
    ----------
    plan_dirs : iterable of Path
        Per-slug plan directories to scan.
    now : datetime, optional
        UTC `now` (injected for tests; default `datetime.utcnow`).
    since : datetime, optional
        Filter: only consider rows with `ts >= since`.
    """
    plan_dirs = list(plan_dirs)
    rows = _gather_log_rows(plan_dirs)
    states = _gather_state_files(plan_dirs)

    if since is not None:
        rows = [
            r
            for r in rows
            if _parse_iso_ts(r.get("ts", "")) and _parse_iso_ts(r.get("ts", "")) >= since
        ]

    # Group by session_id
    sessions: dict[str, list[dict]] = {}
    for row in rows:
        sid = row.get("session_id")
        if not isinstance(sid, str):
            continue
        sessions.setdefault(sid, []).append(row)

    qualifying_sessions: list[str] = []
    for sid, session_rows in sessions.items():
        emitters = {r.get("emitted_by") for r in session_rows}
        if QUALIFYING_EMITTER not in emitters:
            continue
        has_corrupt = any(
            r.get("session_id") == "_recovery" for r in session_rows
        )
        if has_corrupt:
            continue
        has_override = any(
            (r.get("override_in_effect") or {}).get("operator_override_state") is True
            for r in session_rows
        )
        if has_override:
            continue
        qualifying_sessions.append(sid)

    # Manual-correction rows: base-emitter (no --from-develop-plan) + override flag
    manual_corrections: list[str] = []
    for sid, session_rows in sessions.items():
        for r in session_rows:
            if r.get("emitted_by") != BASE_EMITTER:
                continue
            override = r.get("override_in_effect") or {}
            if override.get("operator_override_state") is True:
                manual_corrections.append(sid)
                break

    revision_exercised = _has_revision_path(states)

    # Calendar days since bypass started: earliest bin/chain-overnight ts
    chain_starts = [
        _parse_iso_ts(r.get("ts", ""))
        for r in rows
        if r.get("emitted_by") == CHAIN_DRIVER_EMITTER
    ]
    chain_starts = [t for t in chain_starts if t is not None]
    if chain_starts:
        earliest = min(chain_starts)
        if now is None:
            now = datetime.datetime.now(datetime.timezone.utc)
        if earliest.tzinfo is None:
            earliest = earliest.replace(tzinfo=datetime.timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)
        days = (now - earliest).total_seconds() / 86400.0
    else:
        days = None

    # Derive bypass_status
    if days is not None and days > ESCALATE_DAYS:
        bypass_status = STATUS_ESCALATE_BACKSTOP
    elif (
        len(qualifying_sessions) >= ELIGIBLE_QUALIFYING_RUNS
        and revision_exercised
        and len(manual_corrections) == 0
    ):
        bypass_status = STATUS_ELIGIBLE_TO_EXIT
    else:
        bypass_status = STATUS_INTERIM

    return BypassReport(
        qualifying_run_count=len(qualifying_sessions),
        revision_path_exercised=revision_exercised,
        manual_correction_count=len(manual_corrections),
        calendar_days_since_bypass_started=days,
        bypass_status=bypass_status,
        qualifying_sessions=sorted(qualifying_sessions),
        manual_correction_sessions=sorted(set(manual_corrections)),
    )


def discover_plan_dirs(repo_root: Path) -> list[Path]:
    """Return every `docs/plans/<slug>/` directory under `repo_root`."""
    plans_root = repo_root / "docs" / "plans"
    if not plans_root.is_dir():
        return []
    return [d for d in plans_root.iterdir() if d.is_dir()]


# ---------------------------------------------------------------------------
# CLI entry point — `bin/develop-plan-bypass-status` dispatches here.
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    return project_root()


def _build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="bin/develop-plan-bypass-status",
        description=(
            "Read-only telemetry over `_orchestrator_log.jsonl` + `_state.json` "
            "to drive the develop-plan-bypass exit decision (implplan §E.impl.6)."
        ),
    )
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="emit machine-readable stdout")
    parser.add_argument("--verbose", action="store_true",
                        help="per-qualifying-run breakdown")
    parser.add_argument("--since", default=None,
                        help="restrict scan to rows after this ISO-8601 timestamp")
    return parser


def _format_text_report(report: BypassReport, *, verbose: bool) -> str:
    days = report.calendar_days_since_bypass_started
    days_str = f"{days:.2f}" if isinstance(days, (int, float)) else "n/a"
    lines = [
        f"bypass_status: {report.bypass_status}",
        f"qualifying_run_count: {report.qualifying_run_count}",
        f"revision_path_exercised: {report.revision_path_exercised}",
        f"manual_correction_count: {report.manual_correction_count}",
        f"calendar_days_since_bypass_started: {days_str}",
    ]
    if verbose:
        if report.qualifying_sessions:
            lines.append("")
            lines.append("qualifying_sessions:")
            for sid in report.qualifying_sessions:
                lines.append(f"  - {sid}")
        if report.manual_correction_sessions:
            lines.append("")
            lines.append("manual_correction_sessions:")
            for sid in report.manual_correction_sessions:
                lines.append(f"  - {sid}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    import sys

    parser = _build_parser()
    args = parser.parse_args(argv)
    since: Optional[datetime.datetime] = None
    if args.since:
        since = _parse_iso_ts(args.since)
        if since is None:
            print(f"--since: bad ISO-8601 timestamp {args.since!r}", file=sys.stderr)
            return 1

    plan_dirs = discover_plan_dirs(_repo_root())
    report = compute_bypass_report(plan_dirs, since=since)

    # Stderr WARNING for manual_correction_count > 0 (per E.impl.6 + plan §E.6 line 1577)
    if report.manual_correction_count > 0:
        print(
            f"WARNING: manual_correction_count={report.manual_correction_count}; "
            "mapping may be unreliable",
            file=sys.stderr,
        )

    if args.json_output:
        print(json.dumps(report.to_dict(), sort_keys=True, indent=2))
    else:
        print(_format_text_report(report, verbose=args.verbose), end="")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
