"""`bin/lessons query` + `bin/lessons list` core (per implplan §M.impl.5).

Lenient-parse mode: malformed existing entries (hand-authored, missing
required field) emit a warning but do not crash; the offending block
is dropped from results.
"""

from __future__ import annotations

import pathlib

from .parser import LessonEntry, parse_lessons_md
from .writer import lessons_path, resolve_plan_dir


from bin._env_paths import plans_dir as _env_paths_plans_dir

# The ADOPTER's plan dir, not the plugin's (upstream walked `parents[2]`).
_PLANS_DIR = _env_paths_plans_dir()


class PlanNotFoundError(FileNotFoundError):
    """Raised when `docs/plans/<slug>/` does not exist."""


def _search_fields(entry: LessonEntry) -> tuple[str, ...]:
    """Return the five searchable string fields for keyword matching."""
    return (
        entry.title,
        entry.approach,
        entry.failure_mode,
        entry.rejection,
        entry.reattempt,
    )


def query_lessons(
    slug: str,
    *,
    task: str | None = None,
    keyword: str | None = None,
    plans_dir: pathlib.Path | None = None,
    strict: bool = False,
) -> list[LessonEntry]:
    """Return entries matching the optional filters.

    Parameters
    ----------
    slug : str
        Plan slug; resolves to `docs/plans/<slug>/lessons.md`.
    task : str | None
        Filter by exact `task` field match (e.g., "T3").
    keyword : str | None
        Case-insensitive substring match across title / approach /
        failure_mode / rejection / reattempt.
    plans_dir : pathlib.Path | None
        Override the `docs/plans/` root (for tests).
    strict : bool
        If True, malformed entries raise `LessonsEntryMalformedError`;
        otherwise dropped via lenient-parse mode (default).

    Raises
    ------
    PlanNotFoundError
        If `docs/plans/<slug>/` does not exist.
    """
    plan_dir = resolve_plan_dir(slug, base=plans_dir)
    if not plan_dir.exists():
        raise PlanNotFoundError(
            f"plan directory does not exist: {plan_dir}"
        )

    target = lessons_path(plan_dir)
    if not target.exists():
        return []

    text = target.read_text(encoding="utf-8")
    entries = parse_lessons_md(text, lenient=not strict)

    if task is not None:
        entries = [e for e in entries if e.task == task]

    if keyword is not None:
        kw_lower = keyword.lower()
        entries = [
            e
            for e in entries
            if any(kw_lower in field.lower() for field in _search_fields(e))
        ]

    return entries


def list_lesson_files(
    *,
    slug: str | None = None,
    recent: int | None = None,
    plans_dir: pathlib.Path | None = None,
) -> list[dict[str, object]]:
    """Enumerate `lessons.md` files across plans.

    Parameters
    ----------
    slug : str | None
        Restrict to a single plan slug.
    recent : int | None
        Most-recent N files (sorted by mtime descending).
    plans_dir : pathlib.Path | None
        Override `docs/plans/` root (for tests).

    Returns
    -------
    list[dict]
        Each dict has: ``slug`` (str), ``path`` (str), ``entries``
        (int — count of well-formed entries), ``mtime`` (float).
    """
    plans_root = plans_dir if plans_dir is not None else _PLANS_DIR
    if not plans_root.exists():
        return []

    if slug is not None:
        candidates = [plans_root / slug]
    else:
        candidates = [p for p in plans_root.iterdir() if p.is_dir()]

    rows: list[dict[str, object]] = []
    for plan_dir in candidates:
        target = plan_dir / "lessons.md"
        if not target.is_file():
            continue
        try:
            text = target.read_text(encoding="utf-8")
            entries = parse_lessons_md(text, lenient=True)
        except Exception:  # noqa: BLE001 — count = 0 if parse blows up
            entries = []
        # Best-effort: relative to repo root; if outside (test fixture
        # under tmp_path), fall back to absolute path.
        try:
            rel_path = str(target.relative_to(_REPO_ROOT))
        except ValueError:
            rel_path = str(target)
        rows.append(
            {
                "slug": plan_dir.name,
                "path": rel_path,
                "entries": len(entries),
                "mtime": target.stat().st_mtime,
            }
        )

    rows.sort(key=lambda r: r["mtime"], reverse=True)
    if recent is not None and recent > 0:
        rows = rows[:recent]
    return rows


__all__ = [
    "PlanNotFoundError",
    "query_lessons",
    "list_lesson_files",
]
