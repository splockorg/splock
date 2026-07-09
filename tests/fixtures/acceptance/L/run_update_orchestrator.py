"""Subprocess wrapper for `bin/update_orchestrator` with injectable repo_root.

Used by test_acceptance_L. The real CLI's `_repo_root()` is hardcoded to
the real project root via `Path(__file__).resolve().parents[2]`; this
wrapper overrides it from the test's tmp_path so the subprocess writes
into the fake-repo plan dir, not the live `docs/plans/<slug>/`.

Invocation:
    python run_update_orchestrator.py <fake_repo_root> <slug> <task_id> <status> [...]

Mirrors `bin/update_orchestrator` shell script's behavior (which `cd`s to
the real repo root and execs `python -m bin._update_orchestrator.main`)
except the cwd-derived repo root is replaced by the explicit
`fake_repo_root` arg.
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 5:
        print(
            "usage: run_update_orchestrator.py <fake_repo_root> <slug> <task_id> <status> [...]",
            file=sys.stderr,
        )
        return 1

    fake_repo_root = Path(sys.argv[1]).resolve()
    cli_args = sys.argv[2:]

    # Monkey-patch _repo_root BEFORE invoking main so dispatch picks up
    # the override path.
    from bin._update_orchestrator import main as uo_main

    uo_main._repo_root = lambda: fake_repo_root  # type: ignore[assignment]

    return uo_main.main(cli_args)


if __name__ == "__main__":
    sys.exit(main())
