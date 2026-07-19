"""One-time migration: seed per-slug `_fleet.json` from an operator file.

Port of the reference `seed.py` with the qum roster CONTENT replaced by
an operator-supplied JSON document (the mechanics — idempotence,
`--force`, `--events` — carry over unchanged):

    {
      "as_of": "2026-07-18T00:00:00Z",            // optional
      "states": {
        "<slug>": {"stage": "recon", "status": "ready",
                    "next": "/qa", "blockers": ""}
      },
      "events": [
        {"ts": "...", "slug": "...", "stage": "...", "status": "...",
         "actor": "...", "note": "..."}
      ]
    }

Idempotent by default — a slug whose `_fleet.json` already exists is
skipped (so seeding never clobbers live agent state). `force=True`
overwrites. `piece` / `wave` are joined in from the meta roster.
After seeding, state mutates ONLY via `bin/fleet update` (or the
automatic stage hooks).
"""

from __future__ import annotations

import json
from pathlib import Path

from bin._fleet import engine


class SeedInputError(Exception):
    """The seed document is malformed."""


def _validate(doc: dict) -> None:
    if not isinstance(doc, dict):
        raise SeedInputError("seed document must be a JSON object")
    states = doc.get("states", {})
    if not isinstance(states, dict):
        raise SeedInputError('"states" must be an object of slug -> state')
    for slug, st in states.items():
        if not isinstance(st, dict):
            raise SeedInputError(f"states[{slug!r}] must be an object")
        status = st.get("status")
        if status not in engine.VALID_STATUS:
            raise SeedInputError(
                f"states[{slug!r}].status must be one of "
                f"{sorted(engine.VALID_STATUS)} (got {status!r})"
            )
    events = doc.get("events", [])
    if not isinstance(events, list) or any(not isinstance(e, dict) for e in events):
        raise SeedInputError('"events" must be a list of objects')


def seed_from_file(
    source: Path,
    *,
    force: bool = False,
    events: bool = False,
) -> tuple[int, int, int]:
    """Author per-slug state from `source`. Returns (wrote, skipped, appended)."""
    with open(source, encoding="utf-8") as f:
        doc = json.load(f)
    _validate(doc)

    meta = engine.load_meta()
    roster = meta.get("roster", {})
    as_of = doc.get("as_of") or engine._now_iso()

    wrote = skipped = 0
    for slug, st in doc.get("states", {}).items():
        if engine.load_state(slug) is not None and not force:
            skipped += 1
            continue
        engine.save_state(slug, {
            "slug": slug,
            "piece": roster.get(slug, {}).get("piece", st.get("piece", "")),
            "wave": roster.get(slug, {}).get("wave", st.get("wave")),
            "stage": st.get("stage"),
            "status": st.get("status"),
            "next": st.get("next"),
            "blockers": st.get("blockers", ""),
            "updated": as_of,
            "actor": "fleet-seed",
        })
        wrote += 1

    appended = 0
    if events:
        for e in doc.get("events", []):
            slug = e.get("slug")
            if not slug:
                continue
            engine.append_event(slug, {
                "ts": e.get("ts") or as_of,
                "slug": slug,
                "stage": e.get("stage"),
                "status": e.get("status"),
                "actor": e.get("actor") or "fleet-seed",
                "note": e.get("note") or "",
            })
            appended += 1
    return wrote, skipped, appended
