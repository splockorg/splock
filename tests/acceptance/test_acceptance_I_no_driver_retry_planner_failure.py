"""I.3 — Planner SDK retry exhaustion halts the driver; no driver-side retry loop.

Per userguide §19 #2 + plan §D.5: if the SDK exhausts structured-output
retries, the driver halts with exit code 16 (or matching). No
driver-layer retry loop exists — that would recompute the same prompt
+ re-encounter the same constraint.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.acceptance


def test_two_call_planner_has_no_driver_retry_loop(repo_root):
    """I.3a: bin/_planner/two_call.py has no driver-layer retry loop around SDK calls."""
    two_call_path = repo_root / "bin" / "_planner" / "two_call.py"
    text = two_call_path.read_text(encoding="utf-8")

    # Look for patterns that would suggest driver-side retry around the SDK call:
    # - `for _ in range(...)` near messages.create
    # - `while not result` loops
    # - `try: ... except: continue`
    # - `retry_count =`
    suspicious_patterns = [
        r"for\s+\w+\s+in\s+range\(\s*\d+\s*\)",
        r"while\s+not\s+\w+",
        r"retry_count\s*=",
        r"for_retry\s+in\s+range",
    ]
    import re
    found = []
    for pat in suspicious_patterns:
        if re.search(pat, text):
            found.append(pat)

    # Some retry-shaped patterns may be legitimate (e.g., for-loop over input list).
    # The decisive check is: is messages.create wrapped in retry logic?
    if found:
        # Verify proximity to messages.create.
        lines = text.splitlines()
        sdk_line_indexes = [i for i, line in enumerate(lines) if "messages.create" in line]
        retry_loop_near_sdk = False
        for sdk_idx in sdk_line_indexes:
            # Look at 5 lines before sdk_idx for any retry pattern.
            context_window = "\n".join(lines[max(0, sdk_idx - 5):sdk_idx])
            for pat in suspicious_patterns:
                if re.search(pat, context_window):
                    retry_loop_near_sdk = True
                    break
        assert not retry_loop_near_sdk, (
            f"two_call.py has driver-side retry logic adjacent to messages.create — "
            f"violates plan §D.5 (panic-cascade-resistant choice is to halt)"
        )


def test_planner_exit_codes_include_sdk_retry_exhausted(repo_root):
    """I.3b: planner exit_codes defines the documented halt code."""
    from bin._planner import exit_codes

    # Per userguide §13.3: code 16 (SDK retry exhausted).
    # The constant name varies; look for any halt-on-retry-exhaust constant.
    has_constant = (
        hasattr(exit_codes, "EXIT_SDK_RETRY_EXHAUSTED") or
        hasattr(exit_codes, "EXIT_VERIFY_PLAN_REJECTED") or  # alias per Pass 2 finding
        hasattr(exit_codes, "EXIT_RETRY_EXHAUSTED")
    )
    assert has_constant, (
        "planner exit_codes module lacks any halt-on-retry-exhaust constant; "
        "spec requires explicit code per plan §D.5"
    )
