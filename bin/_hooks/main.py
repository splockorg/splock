"""`bin/hook-log` Python entry — invoked via the POSIX shell wrapper.

CLI shape:

    bin/hook-log <hook-name> <action> "<message>"

Exit codes:
    0  — accepted
    1  — usage error (wrong arg count)
    4  — validation rejected (action not in enum, name empty, etc.)
"""

from __future__ import annotations

import sys

from bin._hooks import HOOK_LOG_ACTIONS
from bin._hooks.log_emit import emit


def _usage() -> str:
    return (
        "usage: bin/hook-log <hook-name> <action> \"<message>\"\n"
        f"  <action> ∈ {sorted(HOOK_LOG_ACTIONS)}\n"
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        sys.stderr.write(_usage())
        return 1
    name, action, message = args
    result = emit(mode="hook", name=name, action=action, message=message)
    if not result.accepted:
        sys.stderr.write(f"hook-log rejected: {result.reason}\n")
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
