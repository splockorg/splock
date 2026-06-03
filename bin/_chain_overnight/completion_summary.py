"""Unified completion-summary emitter — plan §A.5a path 1 (chain driver).

Per implplan §A.impl.7 (lines 753-781) + plan §A.5a (lines 295-388).

Two emit paths share filename pattern + template; this module owns
path 1 (driver-emit on process exit). Path 2 (`bin/update_orchestrator`)
lives in §E.impl and is not built here — but both paths share the
"summary write is LAST" sequencing invariant per orchestrator §4a.4
anchor.

# ====================================================================
# LOAD-BEARING SEQUENCING INVARIANT (orchestrator §4a.4)
# ====================================================================
# Plan §A.5a (plan lines 352-356) specifies:
#
#     "Both emit paths write atomically (write-to-temp + rename); a
#     crash mid-emit leaves either the prior summary (if any) or no
#     file, never a partial one. The summary write is the LAST action
#     of each emit path; if a downstream gesture (e.g., orchestrator
#     transition logging) fails after the summary write, the summary
#     still reflects the run's terminal state."
#
# This module's `emit_chain_summary` MUST end with the summary write.
# Any "downstream gesture" (transition logging, lock cleanup, etc.)
# happens BEFORE the summary write — so if the gesture fails AFTER the
# summary is durable, the summary already reflects the run's state.
#
# Pinned as a test invariant in:
#     tests/splock/test_chain_driver/test_completion_summary.py
#       ::test_summary_write_is_last_action_of_emit_path
#
# Removing that test in a future refactor is a BLOCKER finding at the
# §A post-section Sonnet review junction.
# ====================================================================

Filename pattern:
    `_completion_summary_YYYY-MM-DD_<chain_id_short>.md`

Where `<chain_id_short>` is the last 8 characters of `chain_id`. The
path-1 file is distinguishable from path-2 (`<session_id_short>`)
because the chain_id format embeds a date timestamp.

Template shape:
    1. Resolution summary (task states)
    2. Committed files (`git status --porcelain` snapshot)
    3. Morning-review pointers
    4. Resolution timestamps
    5. ## Chain execution (path-1 only)
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


@dataclass
class ChainPhaseRecord:
    """Per-phase trajectory snapshot for the `## Chain execution` section."""

    phase: int
    phase_command: str  # /plan, /implplan, /code, /test
    started_at: str
    ended_at: str | None
    exit_code: int | None
    cost_usd: float | None
    result: str  # closed-enum per plan §A.8


@dataclass
class CommittedFile:
    """Single porcelain-style entry for the `## Committed files` section."""

    status_code: str  # 'M', 'A', '??', etc.
    path: str


@dataclass
class CompletionSummaryInput:
    """Bundle of context for `emit_chain_summary(...)`.

    Built by `main.py` at finally-block-emit time. All fields optional;
    the template handles missing data gracefully (per plan §A.5a
    success criterion 1 — readable in under 60s, even on halt).
    """

    slug: str
    chain_id: str
    chain_started_at: str
    chain_ended_at: str
    halt_reason: str  # plan §A.5a closed enum
    driver_exit_code: int
    phases: tuple[ChainPhaseRecord, ...] = ()
    committed_files: tuple[CommittedFile, ...] = ()
    morning_review_pointers: tuple[str, ...] = ()
    cost_total_usd: float = 0.0
    wall_clock_cap_seconds: int = 0
    wall_clock_total_seconds: int = 0
    resolution_summary: dict[str, list[str]] = field(default_factory=dict)
    mode_flags: dict[str, bool] = field(default_factory=dict)


def chain_id_short(chain_id: str) -> str:
    """Last 8 characters of `chain_id` — used as filename suffix.

    Per Hole H.2 resolution + plan §A.5a filename pattern: two emit
    paths produce different files on the same day even on the same
    chain because the suffix sources differ (chain_id_short vs.
    session_id_short).
    """
    if len(chain_id) <= 8:
        return chain_id
    return chain_id[-8:]


def summary_filename(chain_id: str, *, today_iso: str | None = None) -> str:
    """Derive the canonical filename for a path-1 emit.

    Pattern: `_completion_summary_YYYY-MM-DD_<chain_id_short>.md`.
    """
    date_str = today_iso or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    return f"_completion_summary_{date_str}_{chain_id_short(chain_id)}.md"


def render_template(payload: CompletionSummaryInput) -> str:
    """Render the markdown body for a completion summary.

    Sections 1-5 per plan §A.5a. Path-1 emit always includes section 5
    (`## Chain execution`); path-2 emit (§E.impl) omits it.

    No external dependencies — pure-Python f-strings. Operator can
    drop a `.claude/templates/completion_summary.md.template` to
    customize the body, but for v2.7 the template lives here for
    cohesion.
    """
    lines: list[str] = []
    lines.append(f"# Completion summary — {payload.slug}")
    lines.append("")
    lines.append(f"- **Chain id:** `{payload.chain_id}`")
    lines.append(f"- **Started:** {payload.chain_started_at}")
    lines.append(f"- **Ended:** {payload.chain_ended_at}")
    lines.append(f"- **Halt reason:** `{payload.halt_reason}`")
    lines.append(f"- **Driver exit code:** `{payload.driver_exit_code}`")
    lines.append("")

    # Section 1 — Resolution summary
    lines.append("## Resolution summary")
    lines.append("")
    if payload.resolution_summary:
        for status, tasks in sorted(payload.resolution_summary.items()):
            lines.append(f"- **{status}** ({len(tasks)}):")
            for task_id in tasks:
                lines.append(f"  - `{task_id}`")
    else:
        lines.append("_No task-state data captured at this halt._")
    lines.append("")

    # Section 2 — Committed files
    lines.append("## Committed files")
    lines.append("")
    if payload.committed_files:
        # Top-level category breakdown per Hole H.25.
        counts: dict[str, int] = {}
        for cf in payload.committed_files:
            counts[cf.status_code] = counts.get(cf.status_code, 0) + 1
        counts_line = ", ".join(
            f"{k}={v}" for k, v in sorted(counts.items())
        )
        lines.append(f"**Categories:** {counts_line}")
        lines.append("")
        lines.append("<details>")
        lines.append("<summary>Full porcelain output</summary>")
        lines.append("")
        lines.append("```")
        for cf in payload.committed_files:
            lines.append(f"{cf.status_code} {cf.path}")
        lines.append("```")
        lines.append("</details>")
    else:
        lines.append("_No file changes committed during this run._")
    lines.append("")

    # Section 3 — Morning-review pointers
    lines.append("## Morning-review pointers")
    lines.append("")
    if payload.morning_review_pointers:
        for ptr in payload.morning_review_pointers:
            lines.append(f"- `{ptr}`")
    else:
        lines.append("_No items routed to morning-review queue._")
    lines.append("")

    # Section 4 — Resolution timestamps
    lines.append("## Resolution timestamps")
    lines.append("")
    lines.append(f"- **Start:** {payload.chain_started_at}")
    lines.append(f"- **End:** {payload.chain_ended_at}")
    lines.append(
        f"- **Wall-clock total:** {payload.wall_clock_total_seconds}s "
        f"(cap: {payload.wall_clock_cap_seconds}s)"
    )
    lines.append("")

    # Section 5 — Chain execution (path-1 only)
    lines.append("## Chain execution")
    lines.append("")
    lines.append(
        f"- **Cost total:** ${payload.cost_total_usd:.4f}"
    )
    if payload.mode_flags:
        flags_str = ", ".join(
            f"{k}={v}" for k, v in sorted(payload.mode_flags.items())
        )
        lines.append(f"- **Mode flags:** {flags_str}")
    lines.append("")
    if payload.phases:
        lines.append("### Per-phase trajectory")
        lines.append("")
        lines.append("| Phase | Command | Started | Ended | Exit | Cost USD | Result |")
        lines.append("|---|---|---|---|---|---|---|")
        for ph in payload.phases:
            cost_str = f"${ph.cost_usd:.4f}" if ph.cost_usd is not None else "-"
            exit_str = str(ph.exit_code) if ph.exit_code is not None else "-"
            lines.append(
                f"| {ph.phase} | {ph.phase_command} | {ph.started_at} | "
                f"{ph.ended_at or '-'} | {exit_str} | {cost_str} | "
                f"`{ph.result}` |"
            )
    else:
        lines.append("_No phase executions completed._")
    lines.append("")
    return "\n".join(lines)


def emit_chain_summary(
    plan_dir: Path,
    payload: CompletionSummaryInput,
    *,
    downstream_finalizer: Any = None,
) -> Path:
    """Path-1 emit — driver-emit on process exit.

    SEQUENCING INVARIANT (orchestrator §4a.4 — anchor §A.5a "summary
    write is LAST"):

    All work that COULD fail (downstream finalizer, logging, etc.) is
    invoked BEFORE the summary write. The atomic write happens LAST.
    If a downstream gesture fails after the summary is durable, the
    summary already reflects the run's terminal state.

    Args:
        plan_dir: target plan directory (must exist).
        payload: structured summary input.
        downstream_finalizer: optional callable invoked BEFORE the
            summary write (e.g., orchestrator transition logging,
            sentinel release). May raise — the summary is NOT written
            if the finalizer raises (callers needing summary-on-failure
            should wrap their finalizer to suppress exceptions).

            Per orchestrator §4a.4: callers that want the summary to
            survive downstream failures MUST invoke their failing-
            gesture AFTER `emit_chain_summary` returns. The contract
            here is: "everything before this returns is durable."

    Returns:
        Path to the written summary file.
    """
    if not plan_dir.exists():
        raise FileNotFoundError(f"plan_dir does not exist: {plan_dir}")
    if not plan_dir.is_dir():
        raise NotADirectoryError(f"plan_dir is not a directory: {plan_dir}")

    body = render_template(payload)
    target = plan_dir / summary_filename(payload.chain_id)

    # Optional downstream finalizer — runs BEFORE the summary write.
    # Per orchestrator §4a.4 anchor, the summary write is the LAST
    # action of this function. Anything that may fail and that the
    # caller wants captured in the summary MUST happen earlier (in the
    # payload construction) — this hook is here only for atomic
    # side-effects that should run before the durable write.
    if downstream_finalizer is not None:
        downstream_finalizer()

    # LAST action — the atomic write.
    from bin._render_plan.atomic_write import write_atomic

    write_atomic(target, body)
    logger.debug("completion summary written to %s", target)
    return target


__all__ = [
    "ChainPhaseRecord",
    "CommittedFile",
    "CompletionSummaryInput",
    "chain_id_short",
    "emit_chain_summary",
    "render_template",
    "summary_filename",
]
