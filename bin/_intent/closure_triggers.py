"""Closure-trigger parser + detectors (implplan §P.impl.7).

Four-shape closed set:
  - pr_merged:<branch>          → git ls-remote + merge-base ancestor
  - commits_landed:<sha_range>  → git rev-list returns empty
  - session_timeout:<duration>  → last_activity_at + parse(d) < NOW()
  - manual_complete             → no detector; operator-invoked only

Open-ended triggers (someday / when_done / eventually / TBD) are
refused at register parse-time with EXIT_INTENT_CLOSURE_TRIGGER_OPEN_ENDED.

Same discipline as §K.6 marker trigger parser, applied to
`agent_sessions.closure_trigger`.
"""

from __future__ import annotations

import datetime
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

from .refusal import OPEN_ENDED_CLOSURE_TRIGGERS


_PR_MERGED_RE = re.compile(r"^pr_merged:[A-Za-z0-9._/\-]+$")
_COMMITS_LANDED_RE = re.compile(
    r"^commits_landed:[A-Fa-f0-9]{7,40}(\.\.[A-Fa-f0-9]{7,40})?$"
)
_SESSION_TIMEOUT_RE = re.compile(r"^session_timeout:(\d+)([mhd])$")
_MANUAL_COMPLETE_RE = re.compile(r"^manual_complete$")


class OpenEndedClosureTriggerError(ValueError):
    """Raised when `register --closure <spec>` is one of the four banned
    open-ended shapes. Caller maps to EXIT_INTENT_CLOSURE_TRIGGER_OPEN_ENDED."""


class MalformedClosureTriggerError(ValueError):
    """Raised when `--closure <spec>` matches none of the four valid shapes."""


@dataclass(frozen=True)
class ClosureTriggerSpec:
    """Parsed closure trigger."""

    raw: str
    shape: str  # one of "pr_merged" | "commits_landed" | "session_timeout" | "manual_complete"
    value: Optional[str] = None
    duration_seconds: Optional[int] = None


def parse(raw: str) -> ClosureTriggerSpec:
    """Parse a closure-trigger spec into a typed shape.

    Raises OpenEndedClosureTriggerError on open-ended shapes.
    Raises MalformedClosureTriggerError on otherwise-invalid shapes.
    """
    if raw in OPEN_ENDED_CLOSURE_TRIGGERS:
        raise OpenEndedClosureTriggerError(
            f"closure_trigger={raw!r} is open-ended; valid shapes: "
            f"pr_merged:<branch> | commits_landed:<sha_range> | "
            f"session_timeout:<duration> | manual_complete"
        )

    if _MANUAL_COMPLETE_RE.match(raw):
        return ClosureTriggerSpec(raw=raw, shape="manual_complete")

    if _PR_MERGED_RE.match(raw):
        branch = raw[len("pr_merged:"):]
        return ClosureTriggerSpec(raw=raw, shape="pr_merged", value=branch)

    if _COMMITS_LANDED_RE.match(raw):
        sha_range = raw[len("commits_landed:"):]
        return ClosureTriggerSpec(raw=raw, shape="commits_landed", value=sha_range)

    m = _SESSION_TIMEOUT_RE.match(raw)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit == "m":
            seconds = n * 60
        elif unit == "h":
            seconds = n * 3600
        elif unit == "d":
            seconds = n * 86400
        else:  # pragma: no cover — regex guards this
            raise MalformedClosureTriggerError(f"unknown duration unit {unit!r}")
        return ClosureTriggerSpec(
            raw=raw, shape="session_timeout", value=f"{n}{unit}",
            duration_seconds=seconds,
        )

    raise MalformedClosureTriggerError(
        f"closure_trigger={raw!r} does not match any of: "
        f"pr_merged:<branch> | commits_landed:<sha_range> | "
        f"session_timeout:<NUM{{m,h,d}}> | manual_complete"
    )


def default_session_timeout(ttl_minutes: int) -> str:
    """Build the default `session_timeout:<duration>` spec from
    `intent.ttl_minutes` knob value."""
    return f"session_timeout:{int(ttl_minutes)}m"


def is_satisfied(
    spec: ClosureTriggerSpec,
    *,
    last_activity_at: Optional[datetime.datetime] = None,
    now: Optional[datetime.datetime] = None,
    git_runner=None,
) -> bool:
    """Return True if the trigger's auto-close condition holds.

    `git_runner` is an optional injection point for tests; defaults to
    `subprocess.run`. `manual_complete` always returns False (operator-only).
    """
    if spec.shape == "manual_complete":
        return False

    if spec.shape == "session_timeout":
        if last_activity_at is None or spec.duration_seconds is None:
            return False
        check_now = now or datetime.datetime.now(datetime.timezone.utc)
        if last_activity_at.tzinfo is None:
            last_activity_at = last_activity_at.replace(tzinfo=datetime.timezone.utc)
        elapsed = (check_now - last_activity_at).total_seconds()
        return elapsed > spec.duration_seconds

    if spec.shape == "pr_merged":
        branch = spec.value or ""
        return _pr_merged_satisfied(branch, runner=git_runner)

    if spec.shape == "commits_landed":
        sha_range = spec.value or ""
        return _commits_landed_satisfied(sha_range, runner=git_runner)

    return False


def _run(cmd: list[str], runner) -> tuple[int, str]:
    """Default subprocess runner shim — replaceable for tests."""
    if runner is not None:
        return runner(cmd)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, check=False,
        )
        return result.returncode, (result.stdout or "")
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def _pr_merged_satisfied(branch: str, *, runner=None) -> bool:
    """`git ls-remote --heads origin <branch>` (branch deleted → satisfied),
    OR `git merge-base --is-ancestor` of branch HEAD into origin/main."""
    if not branch:
        return False
    code_ls, out_ls = _run(["git", "ls-remote", "--heads", "origin", branch], runner)
    if code_ls != 0:
        return False
    if not out_ls.strip():
        # Remote branch deleted/merged — common post-merge state.
        return True
    # Branch still on remote. Check whether its tip is an ancestor of main.
    code_anc, _ = _run(
        ["git", "merge-base", "--is-ancestor", f"origin/{branch}", "origin/main"],
        runner,
    )
    return code_anc == 0


def _commits_landed_satisfied(sha_range: str, *, runner=None) -> bool:
    """`git rev-list <range> ^origin/main` returns empty → all reachable."""
    if not sha_range:
        return False
    code, out = _run(
        ["git", "rev-list", sha_range, "^origin/main"], runner,
    )
    if code != 0:
        return False
    return out.strip() == ""


__all__ = [
    "ClosureTriggerSpec",
    "OpenEndedClosureTriggerError",
    "MalformedClosureTriggerError",
    "parse",
    "default_session_timeout",
    "is_satisfied",
]
