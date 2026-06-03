"""CLI entry for `bin/sealed-rm`.

Operator-authorized deletion of sealed-state files. Three guards
combine to make agent-side invocation impossible in normal Claude
Code operation:

1. TTY check — `sys.stdin.isatty()` must be True. Claude Code's Bash
   tool spawns commands with stdin connected to a pipe (no TTY), so
   the agent invocation fails the check before any deletion happens.
2. `--force` flag required — argparse refuses if absent; operator
   types intent explicitly (no implicit-yes path).
3. Target glob match — every target path must match a pattern in
   `.claude/hooks/sealed_paths.txt`. Unsealed paths are refused with
   a usage hint to use plain `rm`. This keeps the audit trail
   meaningful: every `sealed-rm` row corresponds to a known-sealed
   file deletion.

Audit row emitted per delete via `bin/hook-log sealed-rm {ok|error}
"path=<p> pattern=<glob>"`. Failures emit `error` rows too so the
forensic trail captures refused attempts.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from bin._hooks.sealed_paths import is_sealed, load_sealed_paths


REPO_ROOT = Path(__file__).resolve().parents[2]
SEALED_PATHS_FILE = REPO_ROOT / ".claude" / "hooks" / "sealed_paths.txt"


EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NOT_SEALED = 2
EXIT_NOT_EXIST = 3
EXIT_NOT_TTY = 4


def _hook_log(action: str, message: str) -> None:
    binpath = REPO_ROOT / "bin" / "hook-log"
    if not binpath.exists():
        return
    try:
        subprocess.run(
            [str(binpath), "sealed-rm", action, message[:200]],
            timeout=5,
            check=False,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin/sealed-rm",
        description=(
            "Operator-authorized deletion of sealed-state files. "
            "Requires --force + an operator TTY. Refuses unsealed targets."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Required — affirms operator intent to delete sealed file(s)",
    )
    parser.add_argument(
        "targets",
        nargs="+",
        help="Sealed-path target(s) to delete (each must match sealed_paths.txt)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code != 0 else EXIT_OK

    if not args.force:
        print(
            "bin/sealed-rm: --force is required to delete sealed files",
            file=sys.stderr,
        )
        _hook_log("error", "missing --force flag")
        return EXIT_USAGE

    if not sys.stdin.isatty():
        print(
            "bin/sealed-rm: refusing non-TTY invocation. "
            "This CLI requires an operator terminal (sys.stdin.isatty() == True); "
            "Claude Code's Bash tool runs subprocess commands without a TTY, "
            "which this guard relies on to prevent agent-side deletion.",
            file=sys.stderr,
        )
        _hook_log("error", "non-TTY invocation refused")
        return EXIT_NOT_TTY

    try:
        sealed_patterns = load_sealed_paths(SEALED_PATHS_FILE)
    except FileNotFoundError:
        print(
            f"bin/sealed-rm: sealed_paths.txt missing at {SEALED_PATHS_FILE}",
            file=sys.stderr,
        )
        _hook_log("error", "sealed_paths.txt missing")
        return EXIT_USAGE

    first_failure_code: int | None = None
    for raw_target in args.targets:
        target = Path(raw_target)
        target_str = str(target)

        matched, pattern = is_sealed(target_str, sealed_patterns)
        if not matched:
            print(
                f"bin/sealed-rm: refusing {target_str}: not in sealed inventory; "
                f"use plain rm for unsealed paths",
                file=sys.stderr,
            )
            _hook_log("error", f"not-sealed path={target_str}")
            if first_failure_code is None:
                first_failure_code = EXIT_NOT_SEALED
            continue

        if not target.exists():
            print(
                f"bin/sealed-rm: refusing {target_str}: does not exist on disk",
                file=sys.stderr,
            )
            _hook_log("error", f"not-exist path={target_str}")
            if first_failure_code is None:
                first_failure_code = EXIT_NOT_EXIST
            continue

        try:
            target.unlink()
        except OSError as exc:
            print(
                f"bin/sealed-rm: failed to unlink {target_str}: {exc}",
                file=sys.stderr,
            )
            _hook_log("error", f"unlink-failed path={target_str} err={exc}")
            if first_failure_code is None:
                first_failure_code = EXIT_USAGE
            continue

        print(f"bin/sealed-rm: deleted {target_str} (matched: {pattern})")
        _hook_log("ok", f"deleted path={target_str} pattern={pattern}")

    return first_failure_code if first_failure_code is not None else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
