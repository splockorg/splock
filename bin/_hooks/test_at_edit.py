"""Python backing for `.claude/hooks/test-at-edit.sh` (per implplan §M.impl.6).

PostToolUse hook on Edit|Write. Always exits 0 (PostToolUse cannot
block per R-POSTTOOL-NO-DENY). Logs to `.claude/state/test_at_edit_log.jsonl`.

Decision flow:

1. Read tool_input.file_path from stdin event JSON.
2. Path under `tests/` → silent exit (test-file edit exempt).
3. Path matches sealed-paths inventory → silent exit.
4. Path under `docs/` → silent exit.
5. Path not under a known source directory → silent exit.
6. Discover matching test files via `bin._hooks.test_discovery`.
7. If none → silent exit.
8. Run pytest on each (per-file 60s timeout; total 60s budget).
9. Log per-invocation row to .claude/state/test_at_edit_log.jsonl.
10. Always exit 0.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

from .test_discovery import find_matching_tests


# Total hook wall-clock budget per implplan §M.impl.6 "Performance discipline".
HOOK_TOTAL_BUDGET_SECONDS: int = 60
PER_FILE_TIMEOUT_SECONDS: int = 60

# Source directories under which file edits trigger test discovery. Anything
# outside this list is exempt (silent exit). v2.7 ships Python only.
SOURCE_PREFIXES: tuple[str, ...] = (
    "bin/",
    "extraction/",
    "crawler/",
    "config/",
    "console/",
    "src/",
)

# Exempt prefixes — never trigger discovery even if Python.
EXEMPT_PREFIXES: tuple[str, ...] = (
    "tests/",
    "docs/",
    ".claude/",  # hook itself + skills + templates
    "schemas/",
)


LOG_FILENAME = ".claude/state/test_at_edit_log.jsonl"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _session_id() -> str:
    return os.environ.get("CLAUDE_SESSION_ID") or "sess_00000000"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _path_under(rel: str, prefixes: tuple[str, ...]) -> bool:
    return any(rel == p.rstrip("/") or rel.startswith(p) for p in prefixes)


def _sealed_paths_glob_set(repo_root: Path) -> set[str]:
    """Read sealed_paths.txt entries (best-effort; empty set on failure)."""
    sp = repo_root / ".claude" / "hooks" / "sealed_paths.txt"
    if not sp.exists():
        return set()
    try:
        content = sp.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    out: set[str] = set()
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.add(line)
    return out


def _matches_sealed(rel: str, sealed: set[str]) -> bool:
    """True iff `rel` matches any sealed-paths entry.

    sealed_paths.txt entries can be exact paths or glob-ish patterns
    (e.g., `docs/plans/*/_state.json`). For PostToolUse-side exemption
    we use fnmatch-equivalent matching.
    """
    import fnmatch

    for pattern in sealed:
        if fnmatch.fnmatch(rel, pattern):
            return True
    return False


def _run_pytest(
    test_files: list[Path],
    *,
    repo_root: Path,
    per_file_timeout: int = PER_FILE_TIMEOUT_SECONDS,
    total_budget: int = HOOK_TOTAL_BUDGET_SECONDS,
    pytest_command: list[str] | None = None,
) -> dict[str, object]:
    """Run pytest on each test file; bounded by `total_budget` seconds.

    Returns a result dict: {tests_run, failing, skipped_timeout, duration_seconds}.
    """
    start = time.monotonic()
    tests_run = 0
    failing = 0
    skipped_timeout = False
    for tf in test_files:
        elapsed = time.monotonic() - start
        if elapsed >= total_budget:
            skipped_timeout = True
            break
        remaining = max(1, int(total_budget - elapsed))
        timeout = min(per_file_timeout, remaining)
        cmd = pytest_command or ["pytest"]
        cmd_full = [*cmd, str(tf), "--tb=short", "-q",
                    f"--timeout={timeout}"]
        try:
            proc = subprocess.run(
                cmd_full,
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=timeout + 5,  # small grace beyond pytest's own
            )
        except subprocess.TimeoutExpired:
            skipped_timeout = True
            break
        except (FileNotFoundError, OSError):
            # pytest missing — degrade gracefully.
            return {
                "tests_run": tests_run,
                "failing": failing,
                "skipped_timeout": False,
                "duration_seconds": round(time.monotonic() - start, 3),
                "error": "pytest_unavailable",
            }
        tests_run += 1
        if proc.returncode != 0:
            failing += 1
    return {
        "tests_run": tests_run,
        "failing": failing,
        "skipped_timeout": skipped_timeout,
        "duration_seconds": round(time.monotonic() - start, 3),
    }


def _log_row(repo_root: Path, row: dict) -> None:
    """Append `row` to `.claude/state/test_at_edit_log.jsonl`."""
    log_path = repo_root / LOG_FILENAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n"
    with log_path.open("ab") as fh:
        fh.write(payload.encode("utf-8"))


def process_event(
    event: dict,
    *,
    repo_root: Path | None = None,
    pytest_command: list[str] | None = None,
    use_git_grep: bool = False,
) -> dict[str, object]:
    """Process one PostToolUse stdin event JSON dict.

    Returns a result dict describing what happened. The hook script
    discards stdout (PostToolUse output is informational only).
    """
    root = repo_root or _repo_root()
    file_path = (
        event.get("tool_input", {}).get("file_path")
        or event.get("tool_input", {}).get("path")
    )
    if not file_path:
        return {"action": "skipped", "reason": "no_file_path"}

    src = Path(file_path)
    if not src.is_absolute():
        src = (root / src).resolve()

    try:
        rel = str(src.relative_to(root))
    except ValueError:
        return {"action": "skipped", "reason": "outside_repo", "path": str(src)}

    # Exempt directories.
    if _path_under(rel, EXEMPT_PREFIXES):
        return {"action": "skipped", "reason": "exempt_prefix", "path": rel}

    # Sealed-paths.
    sealed = _sealed_paths_glob_set(root)
    if _matches_sealed(rel, sealed):
        return {"action": "skipped", "reason": "sealed_path", "path": rel}

    # Must be under a source prefix.
    if not _path_under(rel, SOURCE_PREFIXES):
        return {"action": "skipped", "reason": "not_source_prefix", "path": rel}

    # Non-Python — empty discovery → silent skip.
    if src.suffix != ".py":
        return {"action": "skipped", "reason": "non_python", "path": rel}

    matches = find_matching_tests(
        src, repo_root=root, use_git_grep=use_git_grep
    )
    if not matches:
        return {"action": "skipped", "reason": "no_tests_found", "path": rel}

    result = _run_pytest(
        matches, repo_root=root, pytest_command=pytest_command,
    )
    row = {
        "ts": _now_iso(),
        "src": rel,
        "session_id": _session_id(),
        **result,
    }
    _log_row(root, row)
    return {"action": "ran", "row": row, "tests": [str(p) for p in matches]}


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point — reads JSON event from stdin; always exits 0."""
    parser = argparse.ArgumentParser(prog="test-at-edit")
    parser.add_argument("--repo-root", default=None,
                        help="Override repo root (for tests)")
    parser.add_argument("--event-file", default=None,
                        help="Read event JSON from file instead of stdin")
    parser.add_argument("--use-git-grep", action="store_true",
                        help="Enable symbol-grep fallback discovery")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root) if args.repo_root else _repo_root()

    raw = ""
    if args.event_file:
        raw = Path(args.event_file).read_text(encoding="utf-8")
    else:
        try:
            raw = sys.stdin.read()
        except (OSError, ValueError):
            raw = ""
    try:
        event = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        event = {}

    try:
        process_event(
            event,
            repo_root=repo_root,
            use_git_grep=args.use_git_grep,
        )
    except Exception:  # noqa: BLE001 — never block on a hook crash
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
