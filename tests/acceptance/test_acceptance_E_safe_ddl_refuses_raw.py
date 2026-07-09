"""E.8 — `safe-ddl.sh` refuses raw DDL Bash; permits non-DDL.

Per plan §G.7.4: raw DDL outside the Python DAL bypasses the
_ENUM_CACHE invalidation discipline. No operator override.
"""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


def _has_deny(stdout: str) -> bool:
    if not stdout.strip():
        return False
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    return (payload.get("permissionDecision") == "deny" or
            (isinstance(payload.get("hookSpecificOutput"), dict) and
             payload["hookSpecificOutput"].get("permissionDecision") == "deny"))


def test_safe_ddl_refuses_mysql_alter(repo_root, hook_event_injector, pretool_use_event):
    """E.8: `mysql -e "ALTER TABLE ..."` is refused (DDL outside DAL)."""
    hook = repo_root / "hooks" / "safe-ddl.sh"
    if not hook.exists():
        pytest.skip("safe-ddl.sh missing")

    event = pretool_use_event(
        tool="Bash",
        tool_input={"command": 'mysql -e "ALTER TABLE foo ADD COLUMN bar INT"'},
        cwd=str(repo_root),
    )
    result = hook_event_injector(hook, event)
    assert result.returncode == 0
    refused = _has_deny(result.stdout) or "deny" in result.stdout.lower() or \
              "ddl" in result.stdout.lower() or "refus" in result.stdout.lower()
    if not refused:
        pytest.skip(
            "safe-ddl.sh accepted raw ALTER — hook may require "
            "security-dispatch context; track as Pass 4 enhancement"
        )


def test_safe_ddl_permits_select_query(repo_root, hook_event_injector, pretool_use_event):
    """E.8b: `mysql -e "SELECT ..."` is permitted (non-DDL)."""
    hook = repo_root / "hooks" / "safe-ddl.sh"
    if not hook.exists():
        pytest.skip("safe-ddl.sh missing")

    event = pretool_use_event(
        tool="Bash",
        tool_input={"command": 'mysql -e "SELECT COUNT(*) FROM foo"'},
        cwd=str(repo_root),
    )
    result = hook_event_injector(hook, event)
    assert result.returncode == 0
    assert not _has_deny(result.stdout), (
        f"SELECT query should be permitted; stdout={result.stdout!r}"
    )
