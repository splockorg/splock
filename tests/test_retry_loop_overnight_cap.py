"""`unified_counter_active_cap` — the OVERNIGHT_MODE 3→6 branch.

This function had zero coverage, and an agent auditing splock got the belief
about it half wrong in a way the code decides in five lines: the cap is real, but
it bounds the **unified per-task retry counter**, not anything about sealed
paths. Sealed-path violations are an unretried hard deny with no cap at all.

The strict `== "1"` comparison is the part worth pinning. `OVERNIGHT_MODE=true`
and `OVERNIGHT_MODE=yes` read as truthy to a human and to most shell idiom, and
both silently fall through to 3 — a difference an operator only discovers by
being halted three iterations earlier than expected, with nothing naming the
cause.
"""

from __future__ import annotations

import pytest

from bin._retry_loop.iteration_loop import unified_counter_active_cap


def test_default_cap_is_three(monkeypatch):
    monkeypatch.delenv("OVERNIGHT_MODE", raising=False)
    assert unified_counter_active_cap() == 3


def test_overnight_mode_one_raises_the_cap_to_six(monkeypatch):
    monkeypatch.setenv("OVERNIGHT_MODE", "1")
    assert unified_counter_active_cap() == 6


@pytest.mark.parametrize("truthy_looking", ["true", "yes", "01", " 1", "1 ", "TRUE", "on"])
def test_only_the_exact_string_one_raises_the_cap(monkeypatch, truthy_looking: str):
    """Every one of these reads as "on" to an operator and gets the default cap.

    Pinned deliberately rather than fixed: widening the comparison is a behavior
    change to a retry bound, which is a decision, not a cleanup. If someone does
    widen it, this test is where the decision gets made.
    """
    monkeypatch.setenv("OVERNIGHT_MODE", truthy_looking)
    assert unified_counter_active_cap() == 3


def test_explicit_zero_is_the_default_cap(monkeypatch):
    monkeypatch.setenv("OVERNIGHT_MODE", "0")
    assert unified_counter_active_cap() == 3


def test_empty_value_is_the_default_cap(monkeypatch):
    """`OVERNIGHT_MODE=` (exported but empty) is the shape a half-written
    wrapper script produces."""
    monkeypatch.setenv("OVERNIGHT_MODE", "")
    assert unified_counter_active_cap() == 3
