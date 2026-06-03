"""Cross-vertical / cross-repo scope resolver (implplan §L.impl.4).

Reads `config/process_graph.yaml` and resolves staged files to verticals
via `module:` declarations. Refuses when:
  - > 1 distinct vertical touched by the staged diff, OR
  - any file outside the repo root (cross-repo via absolute path / symlink).

Symlink + absolute-path detection mirrors §G.impl.5 pattern (sealed-paths
hook). On parse failure, returns `unknown` for affected files — caller
treats as conservative (no false positives).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

try:
    import yaml  # type: ignore
    _HAVE_YAML = True
except ImportError:  # pragma: no cover — yaml ships with the project venv
    _HAVE_YAML = False


_PROCESS_GRAPH_REL = "config/process_graph.yaml"


def _load_jobs(repo_root: Path, override_path: Optional[Path] = None) -> List[dict]:
    """Load the jobs list from process_graph.yaml. Returns [] on missing/parse-fail."""
    if not _HAVE_YAML:
        return []
    yaml_path = override_path or (repo_root / _PROCESS_GRAPH_REL)
    if not yaml_path.exists():
        return []
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        return []
    return jobs


def _verticals_index(jobs: List[dict]) -> List[Tuple[str, str]]:
    """Return [(module_glob, vertical_id)] sorted by glob specificity desc.

    Each job entry's `module:` is treated as a path prefix (legacy yaml
    shape). Vertical id = job `id:` field. Missing fields yield empty list.
    """
    out: List[Tuple[str, str]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        module = job.get("module")
        jid = job.get("id")
        if not module or not jid:
            continue
        out.append((str(module), str(jid)))
    # Sort by length desc so the most specific prefix wins
    out.sort(key=lambda t: -len(t[0]))
    return out


def resolve_file_to_vertical(
    file_path: str,
    repo_root: Path,
    override_yaml: Optional[Path] = None,
) -> str:
    """Resolve one staged file to a vertical id. Returns `unknown` if no match."""
    jobs = _load_jobs(repo_root, override_yaml)
    idx = _verticals_index(jobs)
    norm = file_path.lstrip("./")
    for prefix, vertical in idx:
        if norm.startswith(prefix.lstrip("./")):
            return vertical
    return "unknown"


def is_cross_repo(file_path: str, repo_root: Path) -> bool:
    """True if `file_path` resolves outside `repo_root`.

    Detects absolute paths whose resolved location is outside the repo,
    and symlinks whose target escapes the repo. Mirrors §G.impl.5 pattern.
    """
    p = Path(file_path)
    try:
        if p.is_absolute():
            real = p.resolve(strict=False)
            return not _path_inside(real, repo_root)
        # Relative paths: join with repo_root, then resolve symlinks
        candidate = (repo_root / p).resolve(strict=False)
        return not _path_inside(candidate, repo_root)
    except OSError:
        return False


def _path_inside(candidate: Path, parent: Path) -> bool:
    """True if `candidate` is the same as or inside `parent` (after resolve)."""
    try:
        parent_real = parent.resolve(strict=False)
        candidate.relative_to(parent_real)
        return True
    except ValueError:
        return False


def check_scope(
    staged_files: Iterable[str],
    repo_root: Path,
    override_yaml: Optional[Path] = None,
) -> Tuple[bool, str, Set[str]]:
    """Check whether the staged set spans >1 vertical or escapes the repo.

    Returns (forced, trigger_kind, details_set) where:
      - forced     : True if a violation was detected
      - trigger    : "cross_vertical" / "cross_repo" / "none"
      - details    : set of offending paths (cross_repo) or verticals (cross_vertical)
    """
    files = list(staged_files)
    if not files:
        return (False, "none", set())

    # Cross-repo first (cheaper to assert + higher severity)
    bad = {f for f in files if is_cross_repo(f, repo_root)}
    if bad:
        return (True, "cross_repo", bad)

    verticals: Set[str] = set()
    for f in files:
        v = resolve_file_to_vertical(f, repo_root, override_yaml)
        if v != "unknown":
            verticals.add(v)
    if len(verticals) > 1:
        return (True, "cross_vertical", verticals)
    return (False, "none", set())
