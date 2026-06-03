"""Python backing for `.claude/hooks/claude-md-discipline.sh`.

Per implplan §M.impl.3. Invoked from the shell wrapper which reads
`git diff --cached --name-only` and pipes content via stdin OR invokes
this module directly with --path / --content args.

Behavior (decision flow per M.impl.3):

1. Enumerate staged CLAUDE.md paths via `git diff --cached --name-only`.
2. If empty → exit 0 silently.
3. For each staged path: read the staged version via `git show :path`.
4. If `lines > HARD_LINE_CEILING` → refuse (stderr JSON; non-zero exit).
5. Else if root `CLAUDE.md` AND `lines > SOFT_LINE_TARGET` → warning.
6. Scan for LLM-emission signatures; refuse if matched (unless --force).
7. exit 0.

`--force` is keyed off the `[force-claude-md]` token in the commit
message (read from `.git/COMMIT_EDITMSG` if present; tests inject via
--force-token).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from .claude_md_constants import (
    FORCE_TOKEN,
    HARD_LINE_CEILING,
    SOFT_LINE_TARGET,
)
from .pattern_detect import scan_llm_emission_signature


EXIT_OK = 0
EXIT_HARD_CEILING = 50  # local scope — not in shared registry (pre-commit gate)
EXIT_LLM_EMISSION = 51
EXIT_AUTO_REGENERATE = 52


def _staged_claude_md_paths(repo_root: Path) -> list[str]:
    """Return staged paths under git diff --cached that end with CLAUDE.md."""
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if proc.returncode != 0:
        return []
    paths: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # Match both root CLAUDE.md and any nested `<dir>/CLAUDE.md`.
        basename = line.rsplit("/", 1)[-1]
        if basename == "CLAUDE.md":
            paths.append(line)
    return paths


def _staged_content(repo_root: Path, path: str) -> str:
    """Read the staged (index) version of `path` via `git show :<path>`."""
    try:
        proc = subprocess.run(
            ["git", "show", f":{path}"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout


def _read_commit_msg(repo_root: Path) -> str:
    """Read .git/COMMIT_EDITMSG if present, else ''."""
    cm = repo_root / ".git" / "COMMIT_EDITMSG"
    if cm.exists():
        try:
            return cm.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""
    return ""


def _is_root_claude_md(path: str) -> bool:
    """True iff `path` is the root CLAUDE.md (not a nested one)."""
    return path == "CLAUDE.md"


def _emit_refusal(payload: dict, *, stream=sys.stderr) -> None:
    print(json.dumps(payload, sort_keys=True), file=stream)


def check_one(
    path: str,
    content: str,
    *,
    commit_msg: str,
    force: bool = False,
) -> tuple[int, list[dict]]:
    """Apply M.impl.3 decision flow to one staged CLAUDE.md path.

    Returns (exit_code, list_of_refusal_payloads). Each payload is a
    dict suitable for stderr-JSON emission. Empty list = clean.

    When `force` is True OR `commit_msg` contains FORCE_TOKEN, all
    refusals are downgraded to warnings and the function returns
    (EXIT_OK, [downgraded_warnings...]).
    """
    force_active = force or (FORCE_TOKEN in commit_msg)
    lines = content.splitlines()
    n_lines = len(lines)
    refusals: list[dict] = []

    # 1. Hard ceiling (all CLAUDE.md, root + nested).
    if n_lines > HARD_LINE_CEILING:
        refusals.append({
            "refusal": "hard_ceiling_exceeded",
            "path": path,
            "lines": n_lines,
            "ceiling": HARD_LINE_CEILING,
        })

    # 2. Soft target (root CLAUDE.md only) — warning, never refusal.
    soft_warning: dict | None = None
    if _is_root_claude_md(path) and n_lines > SOFT_LINE_TARGET:
        soft_warning = {
            "warning": "soft_target_exceeded",
            "path": path,
            "lines": n_lines,
            "soft": SOFT_LINE_TARGET,
        }

    # 3. LLM-emission signature scan.
    sig_matches = scan_llm_emission_signature(content)
    if sig_matches:
        refusals.append({
            "refusal": "llm_emission_signature",
            "path": path,
            "patterns": sorted({pid for pid, _ in sig_matches}),
        })

    # 4. Auto-regenerate attempt — heuristic: commit message contains
    # both a CLAUDE.md path AND a `Tool call:` line.
    if "Tool call:" in commit_msg and path in commit_msg:
        refusals.append({
            "refusal": "auto_regenerate_attempted",
            "path": path,
        })

    if force_active:
        # Downgrade refusals to warnings; keep soft warning if present.
        downgraded = [{**r, "force_override": True} for r in refusals]
        if soft_warning is not None:
            downgraded.append(soft_warning)
        return EXIT_OK, downgraded

    if not refusals:
        # Clean pass; emit soft warning if any.
        return EXIT_OK, [soft_warning] if soft_warning is not None else []

    # Pick the first refusal's exit code (priority: hard > llm > auto-regen).
    code = EXIT_HARD_CEILING
    for r in refusals:
        if r.get("refusal") == "hard_ceiling_exceeded":
            code = EXIT_HARD_CEILING
            break
        if r.get("refusal") == "llm_emission_signature":
            code = EXIT_LLM_EMISSION
        elif r.get("refusal") == "auto_regenerate_attempted" and code == EXIT_HARD_CEILING:
            code = EXIT_AUTO_REGENERATE
    payloads = list(refusals)
    if soft_warning is not None:
        payloads.append(soft_warning)
    return code, payloads


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="claude-md-discipline")
    parser.add_argument("--repo-root", default=None,
                        help="Override repo root (for tests)")
    parser.add_argument("--path", default=None,
                        help="Single path to check (skips git lookup)")
    parser.add_argument("--content-file", default=None,
                        help="Read content from file instead of git index")
    parser.add_argument("--commit-msg", default=None,
                        help="Inline commit message (overrides COMMIT_EDITMSG)")
    parser.add_argument("--force", action="store_true",
                        help="Force operator override")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root) if args.repo_root else Path(__file__).resolve().parents[2]
    commit_msg = args.commit_msg if args.commit_msg is not None else _read_commit_msg(repo_root)

    # Compose target list.
    targets: list[tuple[str, str]] = []
    if args.path:
        path = args.path
        if args.content_file:
            content = Path(args.content_file).read_text(encoding="utf-8")
        else:
            content = _staged_content(repo_root, path)
        targets.append((path, content))
    else:
        for path in _staged_claude_md_paths(repo_root):
            targets.append((path, _staged_content(repo_root, path)))

    if not targets:
        return EXIT_OK

    overall_code = EXIT_OK
    for path, content in targets:
        code, payloads = check_one(
            path, content, commit_msg=commit_msg, force=args.force
        )
        for p in payloads:
            _emit_refusal(p)
        if code != EXIT_OK and overall_code == EXIT_OK:
            overall_code = code
    return overall_code


if __name__ == "__main__":
    sys.exit(main())
