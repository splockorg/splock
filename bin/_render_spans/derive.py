"""Derive spans from `_orchestrator_log.jsonl` + `_chain_sessions.json`.

Per splock implplan §J.impl.3 step 1–8. v2.7 implementation
covers the chain-rooted derivation; hook-log and Task-subagent spawn
correlation paths are included as best-effort but tolerate missing
sources.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from bin._jsonl_log.reader import CorruptRow, iter_rows

from .ordering import sort_spans
from .span_shape import Span, SPAN_ROOT_PARENT


def _read_chain_sessions(plan_dir: pathlib.Path) -> dict[str, Any]:
    target = plan_dir / "_chain_sessions.json"
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_orchestrator_log(plan_dir: pathlib.Path) -> list[dict]:
    target = plan_dir / "_orchestrator_log.jsonl"
    if not target.exists():
        return []
    out: list[dict] = []
    for row in iter_rows(target):
        if isinstance(row, CorruptRow):
            continue
        out.append(row)
    return out


def derive(plan_dir: pathlib.Path) -> list[Span]:
    """Construct OpenInference-shape spans for the given slug directory.

    Step-by-step per §J.impl.3:
      1. Read sources (orchestrator log; chain sessions manifest).
      2. One `chain` root span per chain_id observed.
      3. One `agent` span per phase entry in `_chain_sessions.json`.
      4. Each orchestrator-log transition becomes an event attached to
         its phase span by `chain_id` + `session_id` match.
      5–6. (Hook + Task spawn correlation — best-effort, deferred to NSE
         for full granularity per spec.)
      7. Sort.
    """
    sessions = _read_chain_sessions(plan_dir)
    log_rows = _read_orchestrator_log(plan_dir)

    spans: list[Span] = []
    span_by_chain: dict[str, Span] = {}
    span_by_chain_phase: dict[tuple[str, str], Span] = {}

    # --- Step 2 — chain root spans ---
    chains = sessions.get("chains", {}) if isinstance(sessions, dict) else {}
    for chain_id, manifest in chains.items():
        phases = manifest.get("phases", []) if isinstance(manifest, dict) else []
        start_ts = ""
        end_ts: str | None = None
        status = "unset"
        if phases:
            start_ts = phases[0].get("started_at", "") or ""
            last_end = phases[-1].get("ended_at")
            end_ts = last_end if last_end else None
            status = "ok" if end_ts else "unset"
        if not start_ts:
            # Fallback: derive from earliest log row that mentions this chain.
            for row in log_rows:
                if row.get("chain_id") == chain_id and row.get("ts"):
                    start_ts = row["ts"]
                    break
        if not start_ts:
            # No anchor at all — synthesize minimal so the schema validates.
            start_ts = "1970-01-01T00:00:00Z"
        chain_span = Span(
            trace_id=chain_id,
            parent_span_id=SPAN_ROOT_PARENT,
            span_kind="chain",
            name=f"chain:{chain_id}",
            start_ts=start_ts,
            end_ts=end_ts,
            status=status,
            attributes={"phase_count": len(phases)},
        )
        spans.append(chain_span)
        span_by_chain[chain_id] = chain_span

        # --- Step 3 — agent spans per phase ---
        for phase in phases:
            if not isinstance(phase, dict):
                continue
            pname = phase.get("phase", "unknown")
            pstart = phase.get("started_at") or start_ts
            pend = phase.get("ended_at")
            pstatus = "ok" if pend else "unset"
            phase_span = Span(
                trace_id=chain_id,
                parent_span_id=chain_span.span_id,
                span_kind="agent",
                name=f"phase:{pname}",
                start_ts=pstart,
                end_ts=pend,
                status=pstatus,
                attributes={
                    "phase": pname,
                    "session_id": phase.get("session_id"),
                },
            )
            spans.append(phase_span)
            span_by_chain_phase[(chain_id, pname)] = phase_span

    # --- Step 4 — orchestrator-log transitions as events ---
    for row in log_rows:
        cid = row.get("chain_id")
        if cid is None or cid not in span_by_chain:
            # Non-chain row (e.g. interactive session) — synthesize a
            # session-keyed trace root if needed.
            sid = row.get("session_id")
            if not sid:
                continue
            if sid not in span_by_chain:
                ts = row.get("ts", "1970-01-01T00:00:00Z")
                root = Span(
                    trace_id=sid,
                    parent_span_id=SPAN_ROOT_PARENT,
                    span_kind="agent",
                    name=f"session:{sid}",
                    start_ts=ts,
                    end_ts=None,
                    status="unset",
                    attributes={"non_chain": True},
                )
                spans.append(root)
                span_by_chain[sid] = root
            host = span_by_chain[sid]
        else:
            # Try to attach to phase span first; fall back to chain span.
            host = span_by_chain[cid]
            # Heuristic — if reason includes a phase name token, attach there.
            reason = (row.get("reason") or "")
            for phase_token in ("plan", "implplan", "code", "test"):
                if (cid, phase_token) in span_by_chain_phase and phase_token in reason:
                    host = span_by_chain_phase[(cid, phase_token)]
                    break
        host.events.append(
            {
                "ts": row.get("ts"),
                "event_type": row.get("event_type") or "transition",
                "emitted_by": row.get("emitted_by"),
                "task_id": row.get("task_id"),
                "transition": row.get("transition"),
                "reason": row.get("reason"),
            }
        )

    return sort_spans(spans)


__all__ = ["derive"]
