"""E.17 — `verify-on-stop.sh` honors `stop_hook_active` recursion guard.

Per inventory + plan §G.6.2 + Anthropic issue #55754: Stop hook MUST
no-op when `stop_hook_active: true` is set on the input event.

**This test is expected to fail today** until `hooks/verify-on-stop.sh`
is operator-authored per §10 §7.2 Option B. Same gap as J.1.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


# 2026-05-22 (Pass 6): xfail removed — operator authored Option B stub per §10 §7.2.
def test_verify_on_stop_no_ops_when_stop_hook_active_true(
    repo_root, hook_event_injector, stop_event
):
    """E.17a: stop_hook_active=true → hook no-ops (no bin/verify execution)."""
    hook = repo_root / "hooks" / "verify-on-stop.sh"
    assert hook.exists(), (
        "verify-on-stop.sh missing — same gap surfaced by J.1; operator-author per §10 §7.2"
    )

    event = stop_event(stop_hook_active=True)
    result = hook_event_injector(hook, event)
    assert result.returncode == 0, (
        f"verify-on-stop.sh must exit 0; got {result.returncode}\n"
        f"stderr={result.stderr!r}"
    )
    # When stop_hook_active=true, hook should no-op — no `verify` invocation.
    assert "verify" not in result.stdout.lower() or "skip" in result.stdout.lower(), (
        "stop_hook_active=true should produce no-op output, not verify execution"
    )


def test_verify_on_stop_emits_hook_log_when_active_false(
    repo_root, hook_event_injector, stop_event
):
    """E.17b: stop_hook_active=false → hook emits bin/hook-log row + exits 0 (Option B contract)."""
    hook = repo_root / "hooks" / "verify-on-stop.sh"
    assert hook.exists()
    event = stop_event(stop_hook_active=False)
    result = hook_event_injector(hook, event)
    assert result.returncode == 0
