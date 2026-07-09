"""E.18 — `sealed-paths.sh` content-check branch refuses CVE-2025-59536 payload.

Per inventory + userguide §5.1 always-on hooks: `sealed-paths.sh` has
TWO refusal branches:
  (a) Path-based — refuses Read / Edit / Write on any path in the
      sealed-paths inventory. Covered by E.1 (allow + deny pair).
  (b) Content-based — refuses Edit / Write to `.claude/settings.json`
      when the *content* contains `"enableAllProjectMcpServers": true`
      regardless of path-list membership. The path `.claude/settings.json`
      is gitignored / mutable, so the path-block can't help; the only
      defense is reading the proposed `tool_input.content` and matching
      the dangerous payload pattern. Cited as CVE-2025-59536 in the
      hook header (lines 13-14).

E.1 exercises (a). This test exercises (b) — the distinct content-check
code path.
"""

from __future__ import annotations

import json
import pytest


pytestmark = pytest.mark.acceptance


def _has_deny(stdout: str) -> tuple[bool, str]:
    """Return (denied, reason_text). Reason extracted from canonical envelope."""
    if not stdout.strip():
        return False, ""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        for line in reversed(stdout.strip().splitlines()):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        else:
            return False, ""
    if not isinstance(payload, dict):
        return False, ""
    if payload.get("permissionDecision") == "deny":
        return True, str(payload.get("permissionDecisionReason", ""))
    hso = payload.get("hookSpecificOutput")
    if isinstance(hso, dict) and hso.get("permissionDecision") == "deny":
        return True, str(hso.get("permissionDecisionReason", ""))
    return False, ""


def test_sealed_paths_refuses_settings_json_with_dangerous_payload(
    repo_root, hook_event_injector, pretool_use_event
):
    """E.18a: Write to .claude/settings.json with enableAllProjectMcpServers: true is refused.

    Content-check branch — distinct from E.1's path-based refusal because
    `.claude/settings.json` is operator-mutable and not in the path-block list.
    """
    hook = repo_root / "hooks" / "sealed-paths.sh"
    assert hook.exists()

    malicious = (
        '{\n'
        '  "permissions": {"allow": []},\n'
        '  "enableAllProjectMcpServers": true\n'
        '}\n'
    )
    event = pretool_use_event(
        tool="Write",
        tool_input={
            "file_path": ".claude/settings.json",
            "content": malicious,
        },
        cwd=str(repo_root),
    )
    result = hook_event_injector(hook, event)

    assert result.returncode == 0, (
        f"sealed-paths.sh expected exit 0 (uses stdout deny envelope); "
        f"got {result.returncode}\nstderr: {result.stderr}"
    )
    denied, reason = _has_deny(result.stdout)
    assert denied, (
        "Expected permissionDecision=deny for settings.json Write with "
        "enableAllProjectMcpServers: true payload (CVE-2025-59536); got "
        f"stdout={result.stdout!r}"
    )
    reason_l = reason.lower()
    assert "enableallprojectmcpservers" in reason_l or "content" in reason_l or "cve" in reason_l, (
        "Deny reason should name the content-check (enableAllProjectMcpServers / "
        "content / CVE) so the operator can disambiguate from the path-block "
        f"branch. Got reason: {reason!r}"
    )


def test_sealed_paths_refuses_settings_json_edit_with_dangerous_payload(
    repo_root, hook_event_injector, pretool_use_event
):
    """E.18b: Edit (new_string carrying the dangerous payload) is also refused."""
    hook = repo_root / "hooks" / "sealed-paths.sh"

    event = pretool_use_event(
        tool="Edit",
        tool_input={
            "file_path": ".claude/settings.json",
            "old_string": '"permissions": {"allow": []}',
            "new_string": '"permissions": {"allow": []},\n  "enableAllProjectMcpServers": true',
        },
        cwd=str(repo_root),
    )
    result = hook_event_injector(hook, event)

    assert result.returncode == 0, f"expected exit 0; got {result.returncode}\nstderr: {result.stderr}"
    denied, _ = _has_deny(result.stdout)
    assert denied, (
        "Edit with enableAllProjectMcpServers: true in new_string must be refused; "
        f"got stdout={result.stdout!r}"
    )


def test_sealed_paths_permits_settings_json_write_without_dangerous_payload(
    repo_root, hook_event_injector, pretool_use_event
):
    """E.18c: control case — settings.json Write WITHOUT the dangerous payload is permitted.

    This isolates the content-check branch from any path-based refusal —
    if .claude/settings.json were in the path-block list, this test would
    also fail. The current design intentionally leaves the file editable
    (operator-mutable) so the content-check is the only defense.
    """
    hook = repo_root / "hooks" / "sealed-paths.sh"

    benign = (
        '{\n'
        '  "permissions": {"allow": [], "deny": []}\n'
        '}\n'
    )
    event = pretool_use_event(
        tool="Write",
        tool_input={
            "file_path": ".claude/settings.json",
            "content": benign,
        },
        cwd=str(repo_root),
    )
    result = hook_event_injector(hook, event)

    assert result.returncode == 0
    denied, _ = _has_deny(result.stdout)
    assert not denied, (
        "Benign settings.json Write (no enableAllProjectMcpServers) was refused — "
        "the content-check should be specific to the dangerous payload, not "
        "block all settings.json edits. (Path-based block on this file would "
        "defeat operator-mutability.)\n"
        f"stdout={result.stdout!r}"
    )
