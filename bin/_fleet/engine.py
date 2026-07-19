"""The fleet engine — per-slug state/event IO + the pure render projection.

Faithful port of the qum reference implementation
(`scripts/fleet/fleet.py`, provenance commits a252ef3 + 00f3297). The
per-slug write model and every safety property carry over unchanged in
behavior:

- **Atomic state writes** — `_fleet.json` is written to `.tmp` then
  `os.replace` (atomic swap).
- **Append atomicity** — log lines are `< PIPE_BUF` (4 KiB) single
  `O_APPEND` writes, atomic on local FS; and per-slug, so no
  cross-agent interleave regardless. Notes are clamped at append time
  so the line-size bound holds by construction for arbitrary adopter
  input.
- **Torn-line tolerance** — the fold skips any unparseable log line; a
  partial append never corrupts a render.

Only the path plumbing differs from the reference: everything resolves
through `bin._fleet.paths` (the adopter project) instead of three
repo-pinned constants.
"""

from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime, timezone

from bin._fleet import paths

# status vocabulary → display glyph (mirrors the hub legend)
GLYPH = {
    "ready": "🕛",
    "wip": "✈️",
    "done": "✅",
    "blocked": "❌",
    "parked": "🚫",
    "closed": "✅",
}
VALID_STATUS = set(GLYPH)

# board/now ordering — actionable work first, closed last
STATUS_ORDER = {"wip": 0, "ready": 1, "blocked": 2, "done": 3, "parked": 4, "closed": 5}

MARKERS = {  # zone → (begin, end)
    "now": ("<!-- FLEET:NOW:BEGIN -->", "<!-- FLEET:NOW:END -->"),
    "board": ("<!-- FLEET:BOARD:BEGIN -->", "<!-- FLEET:BOARD:END -->"),
    "recent": ("<!-- FLEET:RECENT:BEGIN -->", "<!-- FLEET:RECENT:END -->"),
}

# Keeps every event line comfortably under PIPE_BUF (4096 bytes) so the
# single O_APPEND write stays atomic on a local FS even with maximal
# non-note fields.
MAX_NOTE_CHARS = 1000

# Updatable state fields, in the reference's merge order.
STATE_FIELDS = ("stage", "status", "next", "blockers", "piece", "wave", "actor")


class HubMarkersMissing(Exception):
    """A `FLEET:*` marker pair is absent from the hub .md."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── per-slug state (overwrite; collision-free — one writer per path) ──────────
def load_state(slug: str) -> dict | None:
    p = paths.state_path(slug)
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_state(slug: str, state: dict) -> None:
    d = paths.slug_dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    # The tmp name is per-PROCESS: in splock the auto stage hooks mean two
    # engines can plausibly write the same slug at once (operator /qa vs a
    # chain /test), and a shared fixed ".tmp" would let one writer steal the
    # other's half-written file. A pid-suffixed tmp keeps the swap atomic
    # under same-path contention too (last writer wins, both states valid).
    tmp = d / f"{paths.STATE_NAME}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, d / paths.STATE_NAME)  # atomic swap


# ── per-slug append-only log (append; collision-free — one writer per path) ───
def append_event(slug: str, event: dict) -> None:
    d = paths.slug_dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    note = event.get("note") or ""
    if len(note) > MAX_NOTE_CHARS:
        event = {**event, "note": note[: MAX_NOTE_CHARS - 1] + "…"}
    line = json.dumps(event, ensure_ascii=False)
    # O_APPEND single-write is atomic for lines < PIPE_BUF (4 KiB) on local FS;
    # and the path is per-slug, so there is no cross-agent contention regardless.
    with open(d / paths.LOG_NAME, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_all_states() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for p in glob.glob(str(paths.plans_dir() / "*" / paths.STATE_NAME)):
        slug = os.path.basename(os.path.dirname(p))
        try:
            with open(p, encoding="utf-8") as f:
                out[slug] = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"warn: skipping {p}: {e}", file=sys.stderr)
    return out


def load_all_events() -> list[dict]:
    events: list[dict] = []
    for p in glob.glob(str(paths.plans_dir() / "*" / paths.LOG_NAME)):
        with open(p, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    events.append(json.loads(ln))
                except json.JSONDecodeError:
                    pass  # a torn/partial line never corrupts the fold
    events.sort(key=lambda e: e.get("ts", ""))
    return events


def load_meta() -> dict:
    p = paths.meta_path()
    if not p.exists():
        return {"waves": [], "roster": {}}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_meta(meta: dict) -> None:
    d = paths.fleet_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{paths.META_NAME}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, paths.meta_path())  # atomic swap


# ── update: the one mutation (no read-modify-write of the hub .md) ────────────
def update(
    slug: str,
    *,
    stage: str | None = None,
    status: str | None = None,
    next_action: str | None = None,
    blockers: str | None = None,
    piece: str | None = None,
    wave: int | None = None,
    actor: str | None = None,
    note: str | None = None,
) -> dict:
    """Merge non-None fields into the slug's state, save it atomically,
    and append one event. Returns the saved state.

    Raises ValueError when the merged status is not in the vocabulary
    (the CLI maps that to a usage exit).
    """
    state = load_state(slug) or {"slug": slug}
    ts = _now_iso()
    incoming = {
        "stage": stage,
        "status": status,
        "next": next_action,
        "blockers": blockers,
        "piece": piece,
        "wave": wave,
        "actor": actor,
    }
    for field in STATE_FIELDS:
        v = incoming[field]
        if v is not None:
            state[field] = v
    if state.get("status") not in VALID_STATUS:
        raise ValueError(
            f"--status must be one of {sorted(VALID_STATUS)} "
            f"(got {state.get('status')!r})"
        )
    state["updated"] = ts
    save_state(slug, state)
    append_event(
        slug,
        {
            "ts": ts,
            "slug": slug,
            "stage": state.get("stage"),
            "status": state.get("status"),
            "actor": actor or "unknown",
            "note": note or "",
        },
    )
    return state


# ── render: pure projection of per-slug files → hub .md marker zones ──────────
def render_board(states: dict[str, dict], meta: dict) -> str:
    roster = meta.get("roster", {})
    rows = ["| Slug | Piece | Stage | Next | Status | Blockers |", "|---|---|---|---|---|---|"]

    # order: actionable first (wip/ready), closed last; then by wave, then slug
    def sort_key(slug: str):
        r = roster.get(slug, {})
        st = states.get(slug, {})
        return (STATUS_ORDER.get(st.get("status"), 9), r.get("wave", 99), slug)

    for slug in sorted(states, key=sort_key):
        st = states[slug]
        r = roster.get(slug, {})
        piece = r.get("piece", st.get("piece", "—"))
        status = st.get("status", "?")
        glyph = GLYPH.get(status, "?")
        label = "closed" if status == "closed" else status
        rows.append(
            f"| `{slug}` | {piece} | {st.get('stage','—')} | {st.get('next','—')} "
            f"| {glyph} {label} | {st.get('blockers','') or '—'} |"
        )
    # closed/archived slugs are static (never change) — rendered from meta, not per-slug files
    for c in meta.get("closed", []):
        rows.append(
            f"| `{c['slug']}` | {c.get('piece','—')} | — | — | ✅ closed "
            f"| {c.get('note','') or '—'} |"
        )
    return "\n".join(rows)


def render_now(states: dict[str, dict], meta: dict) -> str:
    roster = meta.get("roster", {})
    active = [s for s, st in states.items() if st.get("status") in ("wip", "ready")]
    if not active:
        return "_Nothing active — every tracked slug is done, closed, blocked, or parked._"
    active.sort(key=lambda s: (states[s].get("status") != "wip", roster.get(s, {}).get("wave", 99), s))
    rows = ["| Slug | Stage | Next → | Status |", "|---|---|---|---|"]
    for s in active:
        st = states[s]
        rows.append(
            f"| `{s}` | {st.get('stage','—')} | {st.get('next','—')} "
            f"| {GLYPH.get(st.get('status'),'?')} {st.get('status')} |"
        )
    return "\n".join(rows)


def render_recent(events: list[dict], n: int = 8) -> str:
    if not events:
        return "_No events logged yet._"
    rows = ["| When (UTC) | Slug | Stage → status | Actor | Note |", "|---|---|---|---|---|"]
    for e in reversed(events[-n:]):
        note = (e.get("note") or "").replace("|", "\\|")
        if len(note) > 240:
            note = note[:237] + "…"
        rows.append(
            f"| {e.get('ts','')} | `{e.get('slug','')}` "
            f"| {e.get('stage','')} → {e.get('status','')} | {e.get('actor','')} | {note} |"
        )
    return "\n".join(rows)


def render_zones() -> dict[str, str]:
    """Fold every per-slug file into the three zone bodies."""
    states = load_all_states()
    events = load_all_events()
    meta = load_meta()
    return {
        "now": render_now(states, meta),
        "board": render_board(states, meta),
        "recent": render_recent(events),
    }


def _replace_zone(text: str, zone: str, body: str) -> str:
    begin, end = MARKERS[zone]
    i, j = text.find(begin), text.find(end)
    if i == -1 or j == -1 or j < i:
        raise HubMarkersMissing(
            f"markers for zone '{zone}' not found in the hub .md "
            f"(need {begin} … {end}). Run `bin/fleet migrate`, then re-run."
        )
    return text[: i + len(begin)] + "\n" + body + "\n" + text[j:]


def render_hub_write() -> tuple[int, int]:
    """Regenerate the hub's `FLEET:*` zones in place (atomic swap).

    Returns (slug_count, event_count) for the caller's status line.
    Raises HubMarkersMissing before any write when a zone's markers are
    absent — the hub is left byte-identical.
    """
    states = load_all_states()
    events = load_all_events()
    meta = load_meta()
    zones = {
        "now": render_now(states, meta),
        "board": render_board(states, meta),
        "recent": render_recent(events),
    }
    hub = paths.hub_path(meta)
    with open(hub, encoding="utf-8") as f:
        text = f.read()
    for z, body in zones.items():
        text = _replace_zone(text, z, body)
    tmp = f"{hub}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, hub)
    return len(states), len(events)
