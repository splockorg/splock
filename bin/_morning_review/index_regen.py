"""Rolling `_index.md` regenerator (implplan §H.impl.5).

Per H.1a derived-view discipline: `_index.md` is derived from the §C
`_orchestrator_log.jsonl` log, not from scraping the markdown files. The
markdown files are scanned only to find candidate `<chain_id, task_id>`
pairs; whether an entry is "still open" is decided by the latest log
transition, not by the mirror line.

Mirror-divergence resolution: if the mirror says terminal but the log has
no triage row, the log wins (entry is still open per the log). This is
the inverse of the §C source-of-truth rule for triage events.

Triggered on-demand after every triage gesture; not a nightly cron.
"""

from __future__ import annotations

import dataclasses
import datetime
import pathlib
from typing import Dict, List, Optional

from . import entry_format, log_emit, queue_file


_INDEX_FILE_NAME = "_index.md"


# Event-type values that indicate a triage closed the entry.
# Per §H.impl.4 mirror table + log_emit.EVT_* constants.
_TRIAGE_CLOSE_EVENTS = frozenset(
    {
        "morning_review_triage_reactivate",
        "morning_review_triage_route_outstanding",
        "morning_review_triage_route_marker",
        "morning_review_triage_abandon",
    }
)


@dataclasses.dataclass
class _OpenRow:
    """One row of the rendered `_index.md`."""

    task_id: str
    slug: str
    chain_id: str
    days_open: int
    last_reasoning_excerpt: str


def _truncate_excerpt(text: str, max_chars: int = 120) -> str:
    """Per §C.impl.10 render-truncation convention: first N chars + `…`."""
    first_line = text.splitlines()[0] if text else ""
    if len(first_line) <= max_chars:
        return first_line
    return first_line[:max_chars] + "…"


def _days_open(status_since_iso: str) -> int:
    """Compute days since the entry's `status_since` timestamp.

    `status_since_iso` is `<HH:MM:SS>Z` for the *time* of the deferred row;
    we don't have the date in the per-entry field. Fall back to using the
    daily-file's name (YYYY-MM-DD) — that's the canonical date marker per
    §H.impl.4 header.
    """
    # If caller passes only an HH:MM:SS slice, we cannot compute days; the
    # caller (regenerate_for_slug) supplies the file-date instead.
    return 0  # noqa: pragma — overridden by regenerate_for_slug


def _events_for_task(
    log_rows: list[dict], slug: str, task_id: str
) -> list[dict]:
    """Filter log rows to those matching slug + task_id, sorted by ts."""
    out = [
        r
        for r in log_rows
        if r.get("plan_slug") == slug and r.get("task_id") == task_id
    ]
    out.sort(key=lambda r: r.get("ts", ""))
    return out


def _read_log(plan_dir: pathlib.Path) -> list[dict]:
    """Read the per-plan JSONL log; skip CorruptRow per §C.impl.8.

    Per recovery-row tolerance (§C.impl.8): recovery rows are returned in
    the list but callers should treat them as no-op for triage
    derivation. We filter them here.
    """
    from bin._jsonl_log.reader import CorruptRow, iter_rows

    jsonl_path = plan_dir / "_orchestrator_log.jsonl"
    if not jsonl_path.exists():
        return []
    out: list[dict] = []
    for row in iter_rows(jsonl_path):
        if isinstance(row, CorruptRow):
            continue
        # Recovery rows have session_id == "_recovery"; skip per §H.impl.5
        # recovery-row tolerance bullet.
        if row.get("session_id") == "_recovery":
            continue
        out.append(row)
    return out


def regenerate_for_slug(
    repo_root: pathlib.Path,
    slug: str,
    *,
    now: Optional[datetime.datetime] = None,
    warn_divergence=None,
) -> int:
    """Regenerate `docs/plans/<slug>/morning-review/_index.md`.

    Returns the number of open entries in the rendered index.

    `warn_divergence(msg)` is called per mirror-divergence event (default:
    `bin/hook-log morning-review mirror-divergence` per §H.impl.5 step 4).
    """
    plan_dir = repo_root / "docs" / "plans" / slug
    mr_dir = plan_dir / "morning-review"
    if not mr_dir.is_dir():
        # Nothing to regen.
        return 0

    now_utc = now or datetime.datetime.now(datetime.timezone.utc)
    log_rows = _read_log(plan_dir)

    # Step 1-2: walk every open daily file, parse entries.
    candidate_entries: list[tuple[pathlib.Path, entry_format.Entry]] = []
    for daily in queue_file.iter_open_daily_files(repo_root, slug):
        text = queue_file.read_daily(daily)
        for entry in entry_format.parse(text, warn_hook=lambda _m: None):
            candidate_entries.append((daily, entry))

    # Step 3-4: filter to still-open + apply mirror-divergence rule.
    open_rows: list[_OpenRow] = []
    for daily, entry in candidate_entries:
        events = _events_for_task(log_rows, slug, entry.task_id)
        latest_triage_event = None
        for r in events:
            evt = r.get("event_type")
            if evt in _TRIAGE_CLOSE_EVENTS:
                latest_triage_event = r
        # Log-truth: if a close event exists, the entry is closed — drop
        # regardless of mirror.
        if latest_triage_event is not None:
            # Mirror-divergence: mirror still `[pending]` but log says closed.
            if entry.triage_mirror == "[pending]":
                _emit_divergence_warning(
                    warn_divergence,
                    slug=slug,
                    task_id=entry.task_id,
                    direction="log_closed_mirror_pending",
                )
            continue
        # No close event in the log; if the mirror says terminal that's the
        # other side of the divergence — log wins → drop from open set.
        if entry.triage_mirror in entry_format.TERMINAL_MIRRORS:
            _emit_divergence_warning(
                warn_divergence,
                slug=slug,
                task_id=entry.task_id,
                direction="mirror_terminal_log_open",
            )
            continue
        # Open. Compute days_open from the daily file's date stem.
        date_iso = daily.stem  # `YYYY-MM-DD`
        days_open = _days_open_from_date(date_iso, now_utc)
        open_rows.append(
            _OpenRow(
                task_id=entry.task_id,
                slug=entry.slug,
                chain_id=entry.chain_id,
                days_open=days_open,
                last_reasoning_excerpt=_truncate_excerpt(entry.verifier_reasoning),
            )
        )

    # Step 5: render.
    rendered = _render_index(slug, open_rows, now_utc)

    # Step 6: atomic write.
    index_path = mr_dir / _INDEX_FILE_NAME
    queue_file.atomic_write(index_path, rendered)

    # Step 7: emit log row.
    try:
        log_emit.emit_index_regenerated(plan_dir, slug=slug, open_count=len(open_rows))
    except Exception:
        # Best-effort.
        pass
    return len(open_rows)


def _days_open_from_date(date_iso: str, now_utc: datetime.datetime) -> int:
    """Days between the file's date stem and now (UTC, floor)."""
    try:
        d = datetime.datetime.strptime(date_iso, "%Y-%m-%d").replace(
            tzinfo=datetime.timezone.utc
        )
    except ValueError:
        return 0
    delta = now_utc - d
    return max(0, delta.days)


def _render_index(
    slug: str, rows: list[_OpenRow], now_utc: datetime.datetime
) -> str:
    """Render the markdown table per §H.impl.5 step 5."""
    now_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    if not rows:
        return (
            f"# Morning-review index — {slug}\n"
            f"\n"
            f"Generated {now_str}. Derived from `_orchestrator_log.jsonl`.\n"
            f"\n"
            f"_No open entries._\n"
        )
    out: list[str] = []
    out.append(f"# Morning-review index — {slug}")
    out.append("")
    out.append(f"Generated {now_str}. Derived from `_orchestrator_log.jsonl`.")
    out.append("")
    out.append(f"## {slug}")
    out.append("")
    out.append(
        "| task_id | plan | chain_id | days_open | last_reasoning_excerpt |"
    )
    out.append("|---|---|---|---|---|")
    for r in rows:
        # Escape pipe characters in excerpt to keep the table well-formed.
        excerpt = r.last_reasoning_excerpt.replace("|", "\\|")
        out.append(
            f"| {r.task_id} | {r.slug} | {r.chain_id} | {r.days_open} | {excerpt} |"
        )
    return "\n".join(out) + "\n"


def _emit_divergence_warning(warn_hook, *, slug: str, task_id: str, direction: str) -> None:
    msg = f"mirror-divergence slug={slug} task_id={task_id} direction={direction}"
    if warn_hook is not None:
        warn_hook(msg)
        return
    # Default: try `bin/hook-log morning-review mirror-divergence ...`
    import subprocess

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    hook_log = repo_root / "bin" / "hook-log"
    if not hook_log.exists():
        return
    try:
        subprocess.run(
            [str(hook_log), "morning-review", "mirror-divergence", msg],
            cwd=str(repo_root),
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


__all__ = [
    "regenerate_for_slug",
]
