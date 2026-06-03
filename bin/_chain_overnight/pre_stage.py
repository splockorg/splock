"""Pre-stage safety net — driver-layer credential / sealed-path scan.

# Finding 2 dual-altitude safety net (per orchestrator §4a.5 RATIFIED 2026-05-21 shape (a)).
# PreToolUse hooks structurally do NOT fire on the chain driver's own shell `git add` —
# the hook system intercepts only Claude Code subagent tool calls, not the driver
# process's direct shell invocations. This module is therefore the SOLE point where
# credential-shaped paths can be scanned before they hit the git index. The duplication
# with §G's PreToolUse `chain-sealed-state-delete-block` is because of a platform constraint,
# not despite one — agent-path writes are caught by §G; driver-path adds are caught here.

Per implplan §A.impl.6 (lines 718-752) + plan §A.5 commit-phase
description (lines 277-286). The driver's pre-stage safety net is the
second-altitude defense covering the gap that PreToolUse hooks
structurally cannot see. Operator ratification 2026-05-21 (implplan
§A.impl.10 #4) confirmed shape (a) — ship dual-layer.

Module responsibility:
1. Read the blocklist patterns from `.claude/hooks/sealed_paths.txt`
   (owned by §G.impl.2 + §G.impl.5; this module is a CONSUMER).
2. Enumerate the would-be-staged file list from `git diff --name-only
   --cached` + caller-supplied paths.
3. Match every candidate against the blocklist (glob + path-prefix).
4. On any match: return a refuse verdict naming the triggering path.

Caller (phase_spawn.py) handles the halt — writes a `sealed_path_stage_
refused` row to `_orchestrator_log.jsonl`, exits with code 9
(`EXIT_SEALED_PATH_REFUSED` per A.impl.3a).

The blocklist source is `.claude/hooks/sealed_paths.txt`. If the file
doesn't exist (e.g., §G not yet built), this module falls back to a
hard-coded credential blocklist (the credential-shaped patterns from
the implplan cross-cutting sealed-state inventory). Once §G ships, the
file is the source of truth.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Fallback blocklist
# ----------------------------------------------------------------------
# Used when `.claude/hooks/sealed_paths.txt` is absent (§G.impl not yet
# built). Mirrors the cross-cutting sealed-state inventory (implplan
# lines 248-264) + the credential-shaped patterns from plan §A.5
# pre-stage description.
#
# Pattern matching: glob-style via fnmatch — each line is one pattern,
# matched against the basename AND the full relative path.
FALLBACK_BLOCKLIST: tuple[str, ...] = (
    # Credentials
    ".env",
    ".env.*",
    "*.env",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_rsa.*",
    "*.id_rsa",
    "id_ed25519",
    "id_ed25519.*",
    "*credentials*",
    "*.aws/credentials",
    ".aws/credentials",
    ".aws/config",
    # Sealed-state (cross-cutting inventory)
    "*/_chain_sessions.json",
    "*/_chain_running.lock",
    "*/_orchestrator_log.jsonl",
    "*/_state.json",
    "_chain_sessions.json",
    "_chain_running.lock",
    "_orchestrator_log.jsonl",
    "_state.json",
    "_baseline/*",
    "_regression_cases/*",
    # Project secrets
    ".claude/agents/*",
    ".claude/hooks/*",
    ".claude/commands/*",
    # User-home extension (per §G.7.2)
    "*/.aws/*",
    "*/.ssh/*",
    "*/.docker/*",
    "*/.kube/*",
    # Hole H.17
    ".git/*",
)


@dataclass(frozen=True)
class ScanResult:
    """Result of `scan_blocklist(...)`.

    `verdict == "pass"`: no matches; caller may proceed with `git add`.
    `verdict == "refuse"`: at least one match; caller halts with exit 9.
    """

    verdict: str  # "pass" | "refuse"
    matched_paths: tuple[str, ...] = ()
    matched_patterns: tuple[str, ...] = ()

    @property
    def first_match(self) -> tuple[str, str] | None:
        """Convenience: return (path, pattern) of the first match, or None."""
        if not self.matched_paths or not self.matched_patterns:
            return None
        return (self.matched_paths[0], self.matched_patterns[0])


def load_blocklist(repo_root: Path) -> tuple[str, ...]:
    """Load the sealed-paths blocklist.

    Per implplan §A.impl.6: source is `.claude/hooks/sealed_paths.txt`
    (owned by §G.impl). If missing, fall back to FALLBACK_BLOCKLIST.

    The file format is one pattern per line; `#`-prefixed lines are
    comments; blank lines ignored.
    """
    sealed_path_file = repo_root / ".claude" / "hooks" / "sealed_paths.txt"
    if not sealed_path_file.exists():
        logger.debug(
            "sealed_paths.txt absent; using fallback blocklist "
            "(§G.impl not yet shipped)"
        )
        return FALLBACK_BLOCKLIST
    patterns: list[str] = []
    for raw_line in sealed_path_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    if not patterns:
        # File exists but empty — still fall back so we never produce a
        # silent no-defense.
        logger.warning("sealed_paths.txt is empty; using fallback blocklist")
        return FALLBACK_BLOCKLIST
    return tuple(patterns)


def staged_paths(repo_root: Path) -> tuple[str, ...]:
    """Enumerate currently-staged paths via `git diff --name-only --cached`.

    Returns paths relative to `repo_root`. Empty tuple if git unavailable
    or no staged changes.
    """
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", "--cached"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("git diff --cached failed: %s", exc)
        return ()
    if out.returncode != 0:
        logger.warning("git diff --cached exit=%s stderr=%s", out.returncode, out.stderr)
        return ()
    paths = tuple(
        line.strip()
        for line in out.stdout.splitlines()
        if line.strip()
    )
    return paths


def working_tree_paths(repo_root: Path) -> tuple[str, ...]:
    """Enumerate working-tree changes (unstaged + untracked) via porcelain.

    Used as the "would be staged" candidate set when caller invokes
    `git add -A`. The driver's commit step uses `-A`, so all
    working-tree changes are candidates.
    """
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("git status --porcelain failed: %s", exc)
        return ()
    if out.returncode != 0:
        logger.warning(
            "git status --porcelain exit=%s stderr=%s",
            out.returncode, out.stderr,
        )
        return ()
    paths: list[str] = []
    for line in out.stdout.splitlines():
        # Porcelain format: "XY <path>" where XY is the status code.
        # Renamed entries: "R  old -> new" — capture the new path.
        if len(line) < 4:
            continue
        rest = line[3:].strip()
        if " -> " in rest:
            _, after = rest.split(" -> ", 1)
            paths.append(after.strip())
        else:
            paths.append(rest)
    return tuple(paths)


def scan_blocklist(
    candidate_paths: Iterable[str],
    blocklist: Iterable[str],
) -> ScanResult:
    """Scan candidate paths against the blocklist.

    Args:
        candidate_paths: relative paths that would be staged.
        blocklist: glob-style patterns from `.claude/hooks/sealed_paths.txt`
            or `FALLBACK_BLOCKLIST`.

    Returns ScanResult with verdict="pass" if all candidates are clean,
    or verdict="refuse" listing the matches.

    Pattern matching:
    - `fnmatch.fnmatch(path, pattern)` — full-path match
    - `fnmatch.fnmatch(basename, pattern)` — basename match (catches
      patterns like `*.env` against `path/to/file.env`)
    - Path-prefix match for patterns ending in `/*` or `/**`
    """
    blocklist_t = tuple(blocklist)
    matched_paths: list[str] = []
    matched_patterns: list[str] = []
    for path in candidate_paths:
        # Normalize: strip only a leading "./" (NOT leading dots like
        # `.env` — those are part of the filename, not a path prefix).
        if path.startswith("./"):
            path_norm = path[2:]
        else:
            path_norm = path
        basename = os.path.basename(path_norm)
        # Path segments — used for directory-prefix matching ("any segment
        # named <X>"). This catches `docs/plans/<slug>/_baseline/v1/x.jsonl`
        # against pattern `_baseline/*` regardless of nesting depth.
        path_segments = path_norm.split("/")
        for pattern in blocklist_t:
            matched = False
            # Strip trailing /** or /* for prefix-style globs.
            if pattern.endswith("/**"):
                prefix = pattern[:-3]
                if path_norm == prefix or path_norm.startswith(prefix + "/"):
                    matched = True
            if not matched and pattern.endswith("/*"):
                prefix = pattern[:-2]
                # `*/<dirname>/*` matches any path containing /<dirname>/
                if prefix.startswith("*/"):
                    mid = prefix[2:]
                    if f"/{mid}/" in f"/{path_norm}/":
                        matched = True
                if not matched:
                    if path_norm == prefix or path_norm.startswith(prefix + "/"):
                        matched = True
                # Also catch the prefix as ANY-DEPTH directory name. For
                # `_baseline/*` we want to match paths that have a segment
                # equal to `_baseline` followed by anything.
                if not matched and "/" not in prefix:
                    if prefix in path_segments:
                        matched = True
            if not matched and fnmatch.fnmatch(path_norm, pattern):
                matched = True
            if not matched and fnmatch.fnmatch(basename, pattern):
                matched = True
            if matched:
                matched_paths.append(path)
                matched_patterns.append(pattern)
                break
    if matched_paths:
        return ScanResult(
            verdict="refuse",
            matched_paths=tuple(matched_paths),
            matched_patterns=tuple(matched_patterns),
        )
    return ScanResult(verdict="pass")


def scan_for_git_operation(
    repo_root: Path,
    additional_paths: Iterable[str] = (),
) -> ScanResult:
    """High-level convenience: scan staged + working-tree + extras.

    Caller (phase_spawn.py) calls this BEFORE invoking `git add -A`.
    Combines staged + working-tree paths (since `git add -A` stages
    everything) + any caller-supplied additional paths.
    """
    blocklist = load_blocklist(repo_root)
    candidates: list[str] = []
    candidates.extend(staged_paths(repo_root))
    candidates.extend(working_tree_paths(repo_root))
    candidates.extend(p for p in additional_paths if p)
    # Dedup while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for p in candidates:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return scan_blocklist(unique, blocklist)


__all__ = [
    "FALLBACK_BLOCKLIST",
    "ScanResult",
    "load_blocklist",
    "scan_blocklist",
    "scan_for_git_operation",
    "staged_paths",
    "working_tree_paths",
]
