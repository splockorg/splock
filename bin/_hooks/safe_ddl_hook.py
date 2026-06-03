"""Python entry point for the safe-ddl PreToolUse hook.

Per plan §G.7.4 + implplan §G.impl.8.

For inline shapes (`mysql -e`, `psql -c`), the command itself is the
detection surface. For file-shapes (`mysql < migration.sql`,
`psql -f migration.sql`), the .sql file content is scanned for DDL
keywords outside string literals.

Decision RATIFIED 2026-05-20 (implplan §G.impl.16 #2): content scan of
.sql files, not inline-only.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bin._hooks.pattern_detect import scan_ddl_command, sql_file_has_ddl


def _emit_deny(reason: str) -> None:
    envelope = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(envelope) + "\n")
    sys.stdout.flush()


def _hook_log(action: str, message: str) -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    binpath = repo_root / "bin" / "hook-log"
    if not binpath.exists():
        return
    try:
        subprocess.run(
            [str(binpath), "safe-ddl", action, message[:200]],
            timeout=5,
            check=False,
            capture_output=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


REFUSAL_REASON: str = (
    "raw DDL refused — use a Python DAL admin path (with "
    "SPLOCK_DB_ADMIN_USER/PASS) which can pair the DDL with any required "
    "cache invalidation. No operator override; if you need ad-hoc DDL, "
    "invoke the DAL directly from a Python REPL."
)


def main() -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        data = {}

    tool_name = data.get("tool_name") or data.get("tool") or ""
    if tool_name != "Bash":
        _hook_log("ok", f"tool={tool_name} (out-of-scope)")
        return 0
    tool_input = data.get("tool_input", {}) if isinstance(data, dict) else {}
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not command:
        _hook_log("ok", "no command")
        return 0

    match = scan_ddl_command(command)
    if not match:
        _hook_log("ok", "no ddl shape")
        return 0

    pattern_id, excerpt = match
    if pattern_id in ("mysql-inline", "psql-inline"):
        _emit_deny(REFUSAL_REASON + f" cmd={excerpt!r}")
        _hook_log("blocked", f"pattern={pattern_id} cmd={excerpt[:80]}")
        return 0

    # File-shape: scan content.
    if pattern_id in ("mysql-file-redirect", "psql-file-flag"):
        sql_file = Path(excerpt)
        # Tolerate both repo-relative and absolute paths.
        if not sql_file.is_absolute():
            sql_file = Path.cwd() / sql_file
        if not sql_file.exists():
            # File not present yet (would-be redirection?) — refuse on
            # the pattern alone since the agent is plumbing a sql file
            # through the DDL-restricted commands.
            _emit_deny(REFUSAL_REASON + f" file={excerpt!r} (not yet present)")
            _hook_log("blocked", f"pattern={pattern_id} file={excerpt} (absent)")
            return 0
        try:
            content = sql_file.read_text(encoding="utf-8")
        except OSError as exc:
            _emit_deny(REFUSAL_REASON + f" file={excerpt!r} (read failed: {exc})")
            _hook_log("blocked", f"pattern={pattern_id} file={excerpt} read_failed")
            return 0
        if sql_file_has_ddl(content):
            _emit_deny(REFUSAL_REASON + f" file={excerpt!r} contains DDL keywords")
            _hook_log("blocked", f"pattern={pattern_id} file={excerpt}")
            return 0
        # DML-only file: allow.
        _hook_log("ok", f"pattern={pattern_id} file={excerpt} (DML-only)")
        return 0

    _hook_log("ok", f"pattern={pattern_id} (no refusal path)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
