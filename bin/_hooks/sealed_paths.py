"""Sealed-paths glob matcher — shared between hooks + driver.

Per implplan §G.impl.5 / cross-cutting lines 247-263. The canonical
inventory lives in ``.claude/hooks/sealed_paths.txt``; this module loads
it once + matches candidate paths against the glob set.

The driver-side equivalent (`bin/_chain_overnight/pre_stage.py`) reads
the same file and uses similar glob logic — both modules treat
`sealed_paths.txt` as the single source of truth (Finding 2 dual-altitude
defense per orchestrator §4a.5).
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path


def _expand_home(pattern: str) -> str:
    """Expand a leading `~` (or `~/`) to `$HOME`.

    Per §G.7.2 — user-home patterns in sealed_paths.txt are written
    with `~/` prefix. Resolve once at load time.
    """
    if pattern.startswith("~/"):
        return os.path.expanduser("~/") + pattern[2:]
    if pattern == "~":
        return os.path.expanduser("~")
    return pattern


def load_sealed_paths(sealed_paths_file: Path) -> tuple[str, ...]:
    """Load glob patterns from ``.claude/hooks/sealed_paths.txt``.

    Format: one pattern per line; `#` comments; blank lines ignored.
    `~/` prefixes resolved to absolute home paths at load time.

    Raises ``FileNotFoundError`` if the file is missing — callers that
    need a fallback handle that case explicitly (e.g., the driver's
    `pre_stage.py` uses a hard-coded FALLBACK_BLOCKLIST).
    """
    patterns: list[str] = []
    for raw_line in sealed_paths_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(_expand_home(line))
    return tuple(patterns)


def is_sealed(path: str, patterns: tuple[str, ...]) -> tuple[bool, str | None]:
    """True iff `path` matches any sealed pattern.

    Returns (matched, matched_pattern_or_None).

    Matching strategy mirrors `pre_stage.py::scan_blocklist`:
    - Resolve ~ in `path` first.
    - Try full-path fnmatch against pattern.
    - Try basename fnmatch against pattern.
    - For `prefix/**` and `prefix/*` patterns, check path-prefix.
    - For patterns containing `**`, treat as "any depth" match.
    """
    if not path:
        return (False, None)
    # Expand ~ in candidate path.
    if path.startswith("~/"):
        path = os.path.expanduser("~/") + path[2:]
    # Strip leading "./".
    if path.startswith("./"):
        path = path[2:]
    basename = os.path.basename(path)
    path_segments = path.split("/")
    for pattern in patterns:
        # Strip trailing /**.
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if path == prefix or path.startswith(prefix + "/"):
                return (True, pattern)
            # Any-depth directory segment match.
            if "/" not in prefix and prefix in path_segments:
                return (True, pattern)
        # Strip trailing /*.
        if pattern.endswith("/*"):
            prefix = pattern[:-2]
            if prefix.startswith("*/"):
                mid = prefix[2:]
                if f"/{mid}/" in f"/{path}/" or path.startswith(mid + "/"):
                    return (True, pattern)
            elif path == prefix or path.startswith(prefix + "/"):
                return (True, pattern)
            elif "/" not in prefix and prefix in path_segments:
                return (True, pattern)
        # Handle patterns with /** in the middle (e.g., `docs/plans/*/_baseline/**`).
        if "**" in pattern and pattern != "**":
            # Convert /**/ to a wildcard segment.
            regex_pattern = pattern.replace("/**/", "/.*/").replace("**", ".*")
            # Anchor to start; substring match is fine for paths.
            import re as _re
            try:
                if _re.fullmatch(regex_pattern.replace("*", "[^/]*").replace("[^/]*[^/]*", ".*"), path):
                    return (True, pattern)
            except _re.error:
                pass
        # Full-path fnmatch.
        if fnmatch.fnmatch(path, pattern):
            return (True, pattern)
        # Basename fnmatch (catches `*.env` against `path/to/file.env`).
        if fnmatch.fnmatch(basename, pattern):
            return (True, pattern)
    return (False, None)


def repo_root_from_script(script_dir: Path) -> Path:
    """Given a script's directory under `.claude/hooks/`, find repo root."""
    return script_dir.parent.parent


__all__ = [
    "load_sealed_paths",
    "is_sealed",
    "repo_root_from_script",
]
