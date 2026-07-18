"""`bin/fleet board` — the C&C view: one screen for the whole fleet.

Pure fold over per-slug files (states + event log + runs ledger); reads
everything, writes nothing. Total by construction: torn ledger lines
are skipped, a dead runner renders as a died run, a missing
`_fleet.json` renders as `?` — the board never crashes because a child
misbehaved.

Sections:

- **fleet** — per-slug lifecycle (stage/status/next) + newest session
  handle + per-slug spent cost;
- **live children** — runs with an alive runner pid (counted against
  `max_concurrent`);
- **needs attention** — slugs whose lifecycle is `blocked`, whose last
  run errored/was permission-denied, or whose runner died — each with a
  copy-paste resume command (`bin/fleet resume <slug> --directive …`,
  raw `claude -p --resume <sid> …` equivalent shown too);
- **totals** — cumulative `total_cost_usd` across every recorded run
  (all children draw one subscription pool).
"""

from __future__ import annotations

import json

from bin._fleet import engine, runs, spawn


def build_board() -> dict:
    states = engine.load_all_states()
    all_runs = runs.load_all_runs()
    meta = engine.load_meta()

    slugs: dict[str, dict] = {}
    live_children: list[dict] = []
    attention: list[dict] = []
    total_cost = 0.0

    for slug in sorted(set(states) | set(all_runs)):
        st = states.get(slug, {})
        rows = all_runs.get(slug, [])
        live, died, ended = runs.split_runs(rows)
        cost = sum(r.get("total_cost_usd") or 0.0 for r in ended)
        total_cost += cost
        last_end = ended[-1] if ended else None
        session = runs.latest_session_id(slug)

        entry = {
            "slug": slug,
            "stage": st.get("stage"),
            "status": st.get("status"),
            "next": st.get("next"),
            "blockers": st.get("blockers") or "",
            "spawn_directive": st.get("spawn_directive") or "",
            "session_id": session,
            "cost_usd": round(cost, 4),
            "live": len(live),
            "died": len(died),
        }
        slugs[slug] = entry

        for r in live:
            live_children.append({
                "slug": slug, "run_id": r.get("run_id"),
                "stage": r.get("stage"), "pid": r.get("pid"),
                "model": r.get("model"), "effort": r.get("effort"),
                "since": r.get("ts"),
            })

        reasons: list[str] = []
        if st.get("status") == "blocked":
            reasons.append(f"lifecycle blocked: {st.get('blockers') or 'see log'}")
        if last_end is not None:
            if last_end.get("event") == "failed":
                reasons.append(
                    f"last run failed (exit {last_end.get('exit_code')})")
            elif last_end.get("is_error"):
                reasons.append(f"last run errored: {last_end.get('subtype')}")
            elif last_end.get("denials"):
                reasons.append(
                    f"{last_end['denials']} permission denial(s) in last run")
        if died:
            reasons.append(f"{len(died)} runner(s) died without a result")
        if reasons:
            attention.append({
                "slug": slug,
                "reasons": reasons,
                "session_id": session,
                "resume": (f"bin/fleet resume {slug} --directive '<how to proceed>'"
                           if session else None),
                "resume_raw": (f'claude -p --resume {session} "<directive>"'
                               if session else None),
            })

    max_concurrent = meta.get("max_concurrent") or spawn.DEFAULT_MAX_CONCURRENT
    return {
        "slugs": slugs,
        "live": live_children,
        "attention": attention,
        "totals": {
            "cost_usd": round(total_cost, 4),
            "live": len(live_children),
            "max_concurrent": max_concurrent,
        },
    }


def render_text(board: dict) -> str:
    lines: list[str] = []
    totals = board["totals"]
    lines.append(
        f"fleet board — {len(board['slugs'])} slugs · "
        f"{totals['live']}/{totals['max_concurrent']} children live · "
        f"${totals['cost_usd']:.4f} spent"
    )

    if board["slugs"]:
        lines += ["", "  slug · stage/status → next · session · cost"]
        for s in board["slugs"].values():
            glyph = engine.GLYPH.get(s["status"], "?")
            sid = (s["session_id"] or "—")[:8]
            live = f" · {s['live']} live" if s["live"] else ""
            died = f" · {s['died']} died" if s["died"] else ""
            lines.append(
                f"  {s['slug']} · {s['stage'] or '—'}/{glyph} {s['status'] or '?'}"
                f" → {s['next'] or '—'} · {sid} · ${s['cost_usd']:.4f}{live}{died}"
            )

    if board["live"]:
        lines += ["", "live children:"]
        for c in board["live"]:
            cfg = "/".join(str(x) for x in (c["model"], c["effort"]) if x) or "defaults"
            lines.append(
                f"  {c['slug']} [{c['stage']}] pid {c['pid']} ({cfg}) "
                f"since {c['since']} · run {c['run_id']}"
            )

    if board["attention"]:
        lines += ["", "needs attention:"]
        for a in board["attention"]:
            lines.append(f"  {a['slug']}: " + "; ".join(a["reasons"]))
            if a["resume"]:
                lines.append(f"    ↩ {a['resume']}")
                lines.append(f"      ({a['resume_raw']})")
            else:
                lines.append("    ↩ no session recorded — bin/fleet spawn "
                             f"{a['slug']} --stage <stage>")
    else:
        lines += ["", "nothing needs attention."]
    return "\n".join(lines)


def render_json(board: dict) -> str:
    return json.dumps(board, ensure_ascii=False, indent=2)
