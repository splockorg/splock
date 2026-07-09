"""`bin/log` Python entry — invoked via the POSIX shell wrapper.

CLI shape:

    bin/log <emitter> <action> "<message>"

Exit codes:
    0  — accepted
    1  — usage error
    4  — validation rejected (action not in enum, emitter not in
         KNOWN_WRITERS, etc.)
"""

from __future__ import annotations

import sys

from bin._hooks import HOOK_LOG_ACTIONS
from bin._hooks.log_emit import emit
from bin._jsonl_log.writers import KNOWN_WRITERS


def _usage() -> str:
    return (
        "usage: bin/log <emitter> <action> \"<message>\"\n"
        f"  <action> ∈ {sorted(HOOK_LOG_ACTIONS)}\n"
        "  <emitter> must be a registered KNOWN_WRITERS value\n"
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        sys.stderr.write(_usage())
        return 1
    emitter, action, message = args
    result = emit(
        mode="cli",
        name=emitter,
        action=action,
        message=message,
        known_writers=KNOWN_WRITERS,
    )
    if not result.accepted:
        sys.stderr.write(f"bin/log rejected: {result.reason}\n")
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
