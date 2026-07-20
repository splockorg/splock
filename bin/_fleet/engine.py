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
import re
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
    "prompts": ("<!-- FLEET:PROMPTS:BEGIN -->", "<!-- FLEET:PROMPTS:END -->"),
    "tree": ("<!-- FLEET:TREE:BEGIN -->", "<!-- FLEET:TREE:END -->"),
    "attended": ("<!-- FLEET:ATTENDED:BEGIN -->", "<!-- FLEET:ATTENDED:END -->"),
    "board": ("<!-- FLEET:BOARD:BEGIN -->", "<!-- FLEET:BOARD:END -->"),
    "recent": ("<!-- FLEET:RECENT:BEGIN -->", "<!-- FLEET:RECENT:END -->"),
}

#: Zones a hub may lack without failing `render --write`: hubs wired
#: before a zone existed keep working unchanged until `bin/fleet
#: migrate` wires the new markers in (the PROMPTS upgrade precedent;
#: TREE/ATTENDED joined 2026-07-20 — every hand-authored copy of
#: derived hub state rotted twice in one field week). The original
#: three zones stay mandatory.
OPTIONAL_ZONES = ("prompts", "tree", "attended")

# Keeps every event line comfortably under PIPE_BUF (4096 bytes) so the
# single O_APPEND write stays atomic on a local FS even with maximal
# non-note fields.
MAX_NOTE_CHARS = 1000

# Updatable state fields, in the reference's merge order. spawn_directive
# is the splock addition: per-slug operator context that `fleet spawn`
# appends to the child prompt — the one derived-state input the reference
# left in hand-authored prose ("prompt bays"), where it rotted.
STATE_FIELDS = ("stage", "status", "next", "blockers", "piece", "wave", "actor",
                "spawn_directive")


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
    spawn_directive: str | None = None,
) -> dict:
    """Merge non-None fields into the slug's state, save it atomically,
    and append one event. Returns the saved state.

    `spawn_directive=""` clears a stored directive (empty is falsy at
    every read site — spawn and the prompt bay treat it as absent).

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
        "spawn_directive": spawn_directive,
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
    # closed/archived slugs are static (never change) — rendered from meta, not
    # per-slug files. Skip any whose slug still has a LIVE state: `fleet close
    # --no-archive` reconciles the meta while the dir stays live (the observed
    # closed-but-delivered half-state), and the live row must win — one row per
    # slug, never two.
    for c in meta.get("closed", []):
        if c.get("slug") in states:
            continue
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


#: `next` values that map to a runnable spawn one-liner: a single stage
#: command token ("/qa", "/plan", installed-plugin "/splock:code", …).
#: Anything else ("closeout", "—", free prose) is not spawnable.
_SPAWNABLE_NEXT = re.compile(r"^/(?:splock:)?([a-z][a-z0-9_]*)$")

#: Display clamp for stored directives in the prompt bay. The state file
#: keeps the full text (`bin/fleet state <slug>`); the zone stays readable.
MAX_DIRECTIVE_DISPLAY = 600


def _directive_display(state: dict) -> str | None:
    text = " ".join((state.get("spawn_directive") or "").split())
    if not text:
        return None
    if len(text) > MAX_DIRECTIVE_DISPLAY:
        text = text[: MAX_DIRECTIVE_DISPLAY - 1] + "…"
    return text


def unspawnable_stages(meta: dict) -> set[str]:
    """The attended-only stage deny-list (meta `unspawnable_stages`).

    One field drives BOTH enforcement points (field lesson, 2026-07-19/20):
    `spawn` refuses outright, and the PROMPTS zone never renders a
    runnable line for these stages — those slugs render under the
    ATTENDED zone instead. The prior safety was an accident (a missing
    stage profile falling through to deny-writes).
    """
    return set(meta.get("unspawnable_stages") or [])


def next_stage_token(state: dict) -> str | None:
    """The bare stage token in `next` (`/qa` → `qa`), else None."""
    m = _SPAWNABLE_NEXT.match((state.get("next") or "").strip())
    return m.group(1) if m else None


def render_prompts(states: dict[str, dict], meta: dict) -> str:
    """The generated prompt bay: per-slug next actions as runnable
    `bin/fleet spawn` one-liners.

    A one-liner carries ONLY the non-derived inputs (slug + next stage).
    Model/effort/budget resolve from the stage profile and the stored
    `spawn_directive` is applied by `spawn` itself at spawn time, so a
    pasted line can never carry stale config — embedding the directive
    text here would re-capture it at render time, the rot class this
    zone exists to kill. Blocked/parked slugs form the held group with
    their blockers; wip, done, and closed slugs drop automatically.
    """
    roster = meta.get("roster", {})

    def wave_key(slug: str):
        return (roster.get(slug, {}).get("wave", 99), slug)

    def directive_lines(state: dict) -> list[str]:
        d = _directive_display(state)
        return [f"  - directive: {d}"] if d else []

    deny = unspawnable_stages(meta)
    ready = sorted((s for s, st in states.items()
                    if st.get("status") == "ready"
                    and next_stage_token(st) not in deny),
                   key=wave_key)
    held = sorted((s for s, st in states.items()
                   if st.get("status") in ("blocked", "parked")),
                  key=lambda s: (states[s].get("status") != "blocked", *wave_key(s)))
    if not ready and not held:
        return "_Nothing to spawn — no ready or held slugs._"

    lines: list[str] = []
    if ready:
        lines += ["**Ready now** — `spawn` applies the stage profile and the "
                  "stored directive itself; store one with `bin/fleet update "
                  '<slug> --spawn-directive "…"`.', ""]
        for s in ready:
            st = states[s]
            m = _SPAWNABLE_NEXT.match((st.get("next") or "").strip())
            if m:
                lines.append(f"- `bin/fleet spawn {s} --stage {m.group(1)}`")
            else:
                lines.append(f"- `{s}` — next: {st.get('next') or '—'} "
                             "(not a stage command — run by hand)")
            lines += directive_lines(st)
    if held:
        if ready:
            lines.append("")
        lines += ["**Held** — resolve, flip to `ready`, and the spawn line "
                  "appears above.", ""]
        for s in held:
            st = states[s]
            status = st.get("status")
            lines.append(f"- `{s}` — {GLYPH.get(status, '?')} {status}: "
                         f"{st.get('blockers') or 'see log'}")
            lines += directive_lines(st)
    return "\n".join(lines)


def render_tree(states: dict[str, dict], meta: dict) -> str:
    """The generated execution tree — the hand-authored layer that rotted.

    Derived per wave (meta `waves` + roster wave assignments) from
    fields that already exist: status glyph, stage → next, piece. Closed
    slugs render collapsed with their closed date (waved entries under
    their wave; legacy entries without a wave in a trailing group). No
    new authoring surface — flavor a human wants to keep is what
    `--note` already is.
    """
    roster = meta.get("roster", {})
    closed = meta.get("closed", [])
    closed_by_slug = {c.get("slug"): c for c in closed if c.get("slug")}

    def live_line(slug: str) -> str:
        st = states.get(slug)
        if st is None:
            return f"- ⏳ `{slug}` — (no state yet)"
        piece = roster.get(slug, {}).get("piece", st.get("piece") or "—")
        return (f"- {GLYPH.get(st.get('status'), '?')} `{slug}` — "
                f"{st.get('stage', '—')} → {st.get('next', '—')} · {piece}")

    def closed_line(entry: dict) -> str:
        date = entry.get("closed") or ""
        suffix = f" — closed {date}" if date else f" — {entry.get('note') or 'closed'}"
        return f"- ✅ `{entry['slug']}`{suffix}"

    lines: list[str] = []

    def emit_group(title: str, body: list[str]) -> None:
        if body:
            if lines:
                lines.append("")
            lines.extend([f"**{title}**", ""] + body)

    seen: set[str] = set()
    for wave in meta.get("waves", []):
        wid = wave.get("id")
        body: list[str] = []
        for slug in sorted(s for s, r in roster.items() if r.get("wave") == wid):
            seen.add(slug)
            body.append(live_line(slug))
        for entry in closed:
            if entry.get("wave") == wid and entry["slug"] not in seen:
                seen.add(entry["slug"])
                body.append(closed_line(entry))
        emit_group(f"Wave {wid} — {wave.get('title', '')}".rstrip(" —"), body)

    unwaved = sorted((set(roster) | set(states)) - seen)
    emit_group("Unwaved", [live_line(s) for s in unwaved if s not in closed_by_slug])
    seen.update(unwaved)

    legacy_closed = [c for c in closed if c.get("slug") not in seen]
    emit_group("Closed", [closed_line(c) for c in legacy_closed])

    return "\n".join(lines) if lines else "_No waves, roster, or state yet._"


def render_attended(states: dict[str, dict], meta: dict) -> str:
    """The generated attended queue — ready slugs whose next stage is
    attended-only (meta `unspawnable_stages`).

    These never render as runnable spawn lines (the PROMPTS zone skips
    them); here they get the attended session gesture instead, plus the
    optional operator-set `roster.<slug>.attended` config block
    ({slot, model, effort, ultracode} — render what's present, require
    nothing). Full routing-derived assignment is future work (E2) — the
    config block is the seam it will fill.
    """
    deny = unspawnable_stages(meta)
    roster = meta.get("roster", {})
    queue = [(s, st, next_stage_token(st)) for s, st in states.items()
             if st.get("status") == "ready" and next_stage_token(st) in deny]
    if not queue:
        return ("_Nothing queued for attended work._"
                if deny else
                "_No attended-only stages declared (meta `unspawnable_stages`)._")

    def sort_key(item):
        slug, _, _ = item
        att = roster.get(slug, {}).get("attended", {})
        slot = att.get("slot")
        return (slot is None, slot if slot is not None else 0,
                roster.get(slug, {}).get("wave", 99), slug)

    lines = ["**Run attended — one session per slug; never spawned headless.**",
             ""]
    for slug, st, stage in sorted(queue, key=sort_key):
        lines.append(f"- `/splock:{stage} {slug}` — "
                     f"{GLYPH.get(st.get('status'), '?')} {st.get('stage', '—')} "
                     f"→ {st.get('next', '—')}")
        att = roster.get(slug, {}).get("attended", {})
        cfg = " · ".join(f"{k}: {att[k]}" for k in ("slot", "model", "effort", "ultracode")
                         if k in att)
        if cfg:
            lines.append(f"  - {cfg}")
        d = _directive_display(st)
        if d:
            lines.append(f"  - directive: {d}")
    return "\n".join(lines)


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


def _zone_bodies() -> tuple[dict[str, str], dict[str, dict], list[dict]]:
    """One fold for every zone body (shared by print + write paths)."""
    states = load_all_states()
    events = load_all_events()
    meta = load_meta()
    zones = {
        "now": render_now(states, meta),
        "prompts": render_prompts(states, meta),
        "tree": render_tree(states, meta),
        "attended": render_attended(states, meta),
        "board": render_board(states, meta),
        "recent": render_recent(events),
    }
    return zones, states, events


def render_zones() -> dict[str, str]:
    """Fold every per-slug file into the zone bodies."""
    zones, _, _ = _zone_bodies()
    return zones


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
    zones, states, events = _zone_bodies()
    hub = paths.hub_path(load_meta())
    with open(hub, encoding="utf-8") as f:
        text = f.read()
    for z, body in zones.items():
        begin, end = MARKERS[z]
        if z in OPTIONAL_ZONES and begin not in text and end not in text:
            continue  # pre-PROMPTS hub: the three-zone contract stands
        text = _replace_zone(text, z, body)
    tmp = f"{hub}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, hub)
    return len(states), len(events)
