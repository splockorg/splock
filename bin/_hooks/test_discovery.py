"""Test-file discovery for `.claude/hooks/test-at-edit.sh` (per implplan §M.impl.6).

Given a source file path that was just Edited/Written, return a list of
pytest-runnable test file paths that exercise the source. v2.7 ships
Python (pytest) only per M.impl.10 #4 RATIFIED; non-Python sources
return an empty list (hook then exits 0 silently).

Discovery algorithm:

1. Path-based candidate: `<dir>/foo.py` → `tests/<dir>/test_foo.py`.
   - For `bin/_foo/bar.py` → `tests/splock/test_foo/test_bar.py`
     (matches `tests/splock/test_*` convention for §X.impl
     substrate modules); when not found, walk fallback below.
   - For a nested source `<top>/<sub>/run.py` → `tests/<top>/<sub>/test_run.py`.
2. If candidate doesn't exist, symbol-grep fallback: extract top-level
   `def`/`class` names from source, then `git grep -l <symbol> tests/`
   for each. Deduplicate.
3. Non-Python source (no `.py` extension) → empty list.

Scoped per M.impl.10 #4: pytest only; framework-agnostic deferred to v2.66+.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


_TOP_LEVEL_DEF_RE = re.compile(r"^(?:def|class)\s+(\w+)", re.MULTILINE)


def _repo_root() -> Path:
    """Walk up from this file to repo root."""
    # bin/_hooks/test_discovery.py → bin/_hooks → bin → REPO_ROOT
    return Path(__file__).resolve().parents[2]


def _path_candidates(src: Path, repo_root: Path) -> list[Path]:
    """Return ordered list of plausible test-path candidates for `src`."""
    try:
        rel = src.relative_to(repo_root)
    except ValueError:
        # src not under repo root — skip.
        return []
    parts = list(rel.parts)
    if not parts or not parts[-1].endswith(".py"):
        return []
    stem = parts[-1][:-3]  # strip `.py`
    dir_parts = parts[:-1]
    test_basename = f"test_{stem}.py"

    candidates: list[Path] = []
    # Convention 1: direct mirror — `<dir>/foo.py` → `tests/<dir>/test_foo.py`
    candidates.append(repo_root / "tests" / Path(*dir_parts) / test_basename)
    # Convention 2: bin/_foo/bar.py → tests/splock/test_foo/test_bar.py
    if dir_parts and dir_parts[0] == "bin" and len(dir_parts) >= 2:
        sub = dir_parts[1]
        if sub.startswith("_"):
            sub_clean = sub[1:]
            candidates.append(
                repo_root / "tests" / "splock"
                / f"test_{sub_clean}" / test_basename
            )
    # Convention 3: <top>/foo.py → tests/<top>/test_foo.py (mirror w/o
    # subdir) — already covered by candidate 1 in most layouts.

    # Convention 4: <dir>/foo.py → <dir>/test_foo.py (sibling-tests layout).
    candidates.append(repo_root / Path(*dir_parts) / test_basename)

    return candidates


def _extract_top_level_symbols(src_path: Path) -> list[str]:
    """Return top-level `def` / `class` names from `src_path`.

    Returns empty list on read failures.
    """
    try:
        text = src_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return _TOP_LEVEL_DEF_RE.findall(text)


def _git_grep_symbol(symbol: str, repo_root: Path) -> list[Path]:
    """`git grep -l <symbol> tests/` → list of test files. Empty on failure."""
    try:
        proc = subprocess.run(
            ["git", "grep", "-l", "-F", symbol, "tests/"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if proc.returncode not in (0, 1):
        # 0 = matches found; 1 = no matches; >1 = error
        return []
    files: list[Path] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        files.append(repo_root / line)
    return files


def find_matching_tests(
    file_path: Path,
    *,
    repo_root: Path | None = None,
    use_git_grep: bool = True,
) -> list[Path]:
    """Return ordered list of pytest-runnable test files for `file_path`.

    Parameters
    ----------
    file_path : Path
        Absolute path to the edited source file.
    repo_root : Path | None
        Override repo root resolution (for tests).
    use_git_grep : bool
        Whether to invoke the symbol-grep fallback. Tests set False to
        keep the discovery hermetic.
    """
    src = Path(file_path)
    if not src.is_absolute():
        # Resolve relative against the repo root.
        root = repo_root or _repo_root()
        src = (root / src).resolve()
    root = repo_root or _repo_root()

    # Non-Python: empty (per M.impl.10 #4).
    if src.suffix != ".py":
        return []

    # 1. Path-based candidates.
    seen: set[Path] = set()
    out: list[Path] = []
    for cand in _path_candidates(src, root):
        if cand.is_file() and cand not in seen:
            seen.add(cand)
            out.append(cand)
    if out:
        return out

    # 2. Symbol-grep fallback.
    if not use_git_grep:
        return []
    symbols = _extract_top_level_symbols(src)
    # Exclude common boilerplate names that produce excessive matches.
    excluded = {"main", "__init__", "_main", "run", "setUp", "tearDown"}
    symbols = [s for s in symbols if s not in excluded and not s.startswith("_")]
    for sym in symbols:
        for path in _git_grep_symbol(sym, root):
            if path.is_file() and path not in seen:
                seen.add(path)
                out.append(path)
    return out


__all__ = ["find_matching_tests"]
