"""Atomic-append writer for `docs/plans/<slug>/lessons.md`.

Per implplan §M.impl.5 step 3-8: flock + read-modify-write +
tempfile + os.replace + release. Matches §K.impl `list.md` write
discipline.

Lock path: `<plan_dir>/lessons.md.lock`. Distinct from
`_orchestrator_log.jsonl.lock` — concurrent JSONL emission via
`log_emit.py` does not contend with the lessons.md write itself; the
two locks compose in §C.impl.5 compound-write order (lessons.md.lock
acquired here, then JSONL write happens AFTER the file is durable).
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import pathlib
import tempfile
from typing import Iterator

from .parser import LessonEntry


LESSONS_BASENAME = "lessons.md"
LOCKFILE_SUFFIX = ".lock"

from bin._env_paths import plans_dir as _env_paths_plans_dir
from bin._env_paths import project_root as _env_paths_project_root

# `lessons.md` is adopter data; the entry template is a plugin-shipped asset.
# Upstream resolves BOTH off `parents[2]`, which under an installed plugin is
# the plugin cache — so lessons would be written into the plugin tree.
_PLANS_DIR = _env_paths_plans_dir()

_TEMPLATES_REL = pathlib.Path(".claude") / "templates"
_TEMPLATE_BASENAME = "lessons_entry.md.template"


def _template_path() -> pathlib.Path:
    """Resolve the entry template: adopter-repo override first, plugin second.

    Mirrors `bin/_render_plan/md_renderer._template_path` — the fallback is PER
    FILE, so an adopter may override this one template without shadowing the
    rest of the shipped set. When neither exists, the plugin-shipped path is
    returned so the caller's error names the canonical location.
    """
    project_candidate = _env_paths_project_root() / _TEMPLATES_REL / _TEMPLATE_BASENAME
    if project_candidate.is_file():
        return project_candidate
    plugin_root = pathlib.Path(__file__).resolve().parents[2]
    return plugin_root / _TEMPLATES_REL / _TEMPLATE_BASENAME


class AtomicWriteFailedError(IOError):
    """Wraps any IOError that breaks the temp+rename sequence."""


def lessons_path(plan_dir: pathlib.Path) -> pathlib.Path:
    return plan_dir / LESSONS_BASENAME


def lockfile_path(plan_dir: pathlib.Path) -> pathlib.Path:
    return plan_dir / (LESSONS_BASENAME + LOCKFILE_SUFFIX)


def resolve_plan_dir(slug: str, *, base: pathlib.Path | None = None) -> pathlib.Path:
    """Resolve `docs/plans/<slug>/` from a slug string.

    Does not check existence; callers decide whether to refuse with
    EXIT_PLAN_NOT_FOUND (2) or auto-create.
    """
    plans_dir = base if base is not None else _PLANS_DIR
    return plans_dir / slug


@contextlib.contextmanager
def acquire_exclusive(plan_dir: pathlib.Path) -> Iterator[int]:
    """LOCK_EX on `<plan_dir>/lessons.md.lock`. Auto-creates plan_dir."""
    plan_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lockfile_path(plan_dir)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield fd
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _read_template() -> str:
    path = _template_path()
    if not path.exists():
        raise AtomicWriteFailedError(f"lessons entry template missing at {path}")
    return path.read_text(encoding="utf-8")


def render_entry(entry: LessonEntry, *, template: str | None = None) -> str:
    """Render a `LessonEntry` via the template at
    `.claude/templates/lessons_entry.md.template`.
    """
    tpl = template if template is not None else _read_template()
    return tpl.format(
        date=entry.date,
        title=entry.title,
        task=entry.task,
        approach=entry.approach,
        failure_mode=entry.failure_mode,
        rejection=entry.rejection,
        reattempt=entry.reattempt,
        source=entry.source,
    )


def _atomic_write(target: pathlib.Path, content: str) -> None:
    """Tempfile + fsync + os.replace in `target.parent`."""
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target)
    except Exception as exc:
        # Cleanup tempfile on any failure.
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)
        raise AtomicWriteFailedError(
            f"atomic write to {target} failed: {exc!r}"
        ) from exc


def append_lesson(
    slug: str,
    entry: LessonEntry,
    *,
    plans_dir: pathlib.Path | None = None,
) -> pathlib.Path:
    """Append `entry` to `docs/plans/<slug>/lessons.md` atomically.

    Holds `lessons.md.lock` for the full read-modify-write cycle.
    Returns the path written.

    Raises
    ------
    AtomicWriteFailedError
        On any failure during the tempfile+rename sequence.
    """
    plan_dir = resolve_plan_dir(slug, base=plans_dir)
    rendered = render_entry(entry)
    target = lessons_path(plan_dir)

    with acquire_exclusive(plan_dir):
        existing = ""
        if target.exists():
            existing = target.read_text(encoding="utf-8")
        # Newline-separated H2 blocks. If existing content doesn't end
        # in a blank line, add one before the new block.
        if existing and not existing.endswith("\n\n"):
            if existing.endswith("\n"):
                separator = "\n"
            else:
                separator = "\n\n"
        else:
            separator = ""
        new_content = existing + separator + rendered
        if not new_content.endswith("\n"):
            new_content += "\n"
        _atomic_write(target, new_content)

    return target


__all__ = [
    "LESSONS_BASENAME",
    "AtomicWriteFailedError",
    "lessons_path",
    "lockfile_path",
    "resolve_plan_dir",
    "acquire_exclusive",
    "render_entry",
    "append_lesson",
]
