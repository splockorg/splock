"""Hub scaffolding + one-time migration of an existing hand-edited hub.

Port of the reference `migrate_launcher.py` with the qum-specific
anchors generalized to operator-supplied ones. The safety property is
kept verbatim in behavior: **every** anchor is verified before a
**single** atomic read→transform→swap; a missing anchor refuses with
the full missing list and leaves the hub byte-identical. Re-running is
a no-op once markers exist (idempotent).

Two entry points:

- `init(...)`  — the opt-in switch. Writes `_fleet_meta.json` (waves /
  roster / closed / legend + the hub path) and, unless an existing hub
  is registered via `hub`, scaffolds a fresh generated hub at
  `docs/plans/_fleet/fleet.md`.
- `migrate(...)` — wires the `FLEET:*` marker zones + the fleet
  protocol section into the REGISTERED hub. Zones with operator
  anchors replace the text between them (both anchor strings are
  preserved — anchors here are stable section headers, not volatile
  table bodies); zones without anchors land in an appended generated
  section.
"""

from __future__ import annotations

import os
from pathlib import Path

from bin import _env_paths
from bin._fleet import engine, paths


class AnchorsMissing(Exception):
    """One or more migrate anchors were not found; nothing was written."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(
            "anchor(s) not found in the hub .md (nothing written): "
            + "; ".join(repr(m) for m in missing)
        )


PROTOCOL_MARKER = "<!-- FLEET:PROTOCOL -->"

PROTOCOL = f"""{PROTOCOL_MARKER}
## ⚙️ Fleet protocol (concurrency-safe)

**Status lives in per-slug files, NOT this .md.** Never hand-edit the `FLEET:*`
zones (▶ Now, the prompt bay, the status board, recent events) — they are
generated. State is updated with ONE command (a fast, collision-free append —
no read-modify-write of this file):

```bash
bin/fleet update <slug> --stage <stage> --status <status> \\
    --next "<next action>" --actor <who> --note "<one line>"
bin/fleet render --write          # regenerate the derived zones
```

- **status** ∈ `ready` (next stage runnable) · `wip` (a stage in flight) ·
  `done` (pipeline gates cleared, closeout separate) · `blocked` · `parked` ·
  `closed` (archived).
- **In splock you rarely run `update` by hand:** every stage engine and stage
  command (`/recon`, `/qa`, `/plan`, `/implplan`, `/code`, `/test`, `/review`,
  …) records `wip` on stage start and `ready --next <next stage>` (or `done` /
  `closed`) on completion automatically. Manual updates are for out-of-band
  changes only (`parked`, `blocked` with a reason, roster corrections).
- Each update appends an event to `docs/plans/<slug>/_fleet_log.jsonl` (the
  history behind Recent events) and atomically overwrites
  `docs/plans/<slug>/_fleet.json` (current state).
- **Why this is contention-free:** per-slug files have no shared write target,
  so any number of agents update concurrently — no clobbering, no
  stale-snapshot re-reads of this file.
- **Next actions are generated too:** the prompt bay zone renders a
  `bin/fleet spawn` one-liner per ready slug — never hand-author paste blocks.
  Per-slug spawn context is stored with `bin/fleet update <slug>
  --spawn-directive "…"` (one-shot: the stage that consumed it clears it on
  completion) and applied by `spawn` itself.
- Cross-slug structure (waves · roster · legend) is
  `docs/plans/_fleet/_fleet_meta.json` (rare, single author).
- Hand-authored narrative stays hand-editable; only `FLEET:*` zones are
  generated. Engine: `bin/fleet` (`bin/_fleet/`).
"""

SECTION_MARKER = "<!-- FLEET:SECTION -->"

_ZONE_HEADINGS = {
    "now": "### ▶ Now — actionable slugs",
    "prompts": "### 🎛 Prompt bay — next actions",
    "tree": "### 🌳 Execution tree",
    "attended": "### ⏸ Attended queue",
    "board": "### 📋 Status board",
    "recent": "### 🕘 Recent events",
}


def _zone_block(zone: str) -> str:
    begin, end = engine.MARKERS[zone]
    return f"{begin}\n{end}"


def _generated_section(zones: list[str]) -> str:
    parts = [
        SECTION_MARKER,
        "## 🛰️ Fleet status",
        "",
        "_Generated from per-slug `_fleet.json` files — never hand-edit the"
        " marked zones; run `bin/fleet render --write`._",
    ]
    for zone in zones:
        parts += ["", _ZONE_HEADINGS[zone], "", _zone_block(zone)]
    return "\n".join(parts) + "\n"


def _upgrade_blocks(zones: list[str]) -> str:
    """Marker blocks appended when SOME zones are already wired (a hub
    migrated before a zone existed): one compact sub-section per zone,
    no duplicate `## 🛰️ Fleet status` header."""
    parts: list[str] = []
    for zone in zones:
        parts += [
            _ZONE_HEADINGS[zone],
            "",
            "_Generated zone — never hand-edit; run `bin/fleet render --write`._",
            "",
            _zone_block(zone),
            "",
        ]
    return "\n".join(parts)


DEFAULT_LEGEND = {
    "ready": "🕛 runnable now",
    "wip": "✈️ in flight",
    "done": "✅ stage gates cleared (closeout separate)",
    "blocked": "❌ blocked",
    "parked": "🚫 parked/cancelled",
    "closed": "✅ slug done + archived",
}

_META_COMMENT = (
    "fleet cross-slug structure (rarely changes; single author). Per-slug "
    "DYNAMIC state lives in docs/plans/<slug>/_fleet.json; history in "
    "docs/plans/<slug>/_fleet_log.jsonl. The hub .md FLEET:* zones are "
    "DERIVED from these by `bin/fleet render --write`. This meta is written "
    "ONLY by `bin/fleet init`, `migrate`, and `close` (the terminal-"
    "transition verb: roster -> closed[], successor mint); hand edits are "
    "for the operator-owned knobs (roster/waves/profiles/unspawnable_stages/"
    "attended blocks)."
)


def _scaffold_hub_text(project_name: str) -> str:
    return (
        f"# {project_name} — fleet status hub\n"
        "\n"
        "_This file is the generated human view of the fleet lifecycle "
        "tracker. Narrative sections you add outside the marked zones are "
        "yours; the zones below are build artifacts._\n"
        "\n"
        f"{_generated_section(list(engine.MARKERS))}"
        "\n"
        f"{PROTOCOL}"
    )


def _atomic_write_text(target: Path, text: str) -> None:
    tmp = f"{target}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, target)


def init(hub: str | None = None) -> tuple[bool, Path]:
    """Opt the current project in. Returns (created, hub_path).

    Idempotent: when the meta file already exists nothing is written
    and `created` is False. When `hub` names an existing .md it is
    registered as the render target (its zones are wired later by
    `migrate`); otherwise a fresh generated hub is scaffolded at the
    default location, marker zones included.
    """
    if paths.enabled():
        return False, paths.hub_path(engine.load_meta())

    project_root = _env_paths.project_root()
    if hub is not None:
        hub_abs = (project_root / hub) if not os.path.isabs(hub) else Path(hub)
        if not hub_abs.is_file():
            raise FileNotFoundError(f"--hub does not exist: {hub_abs}")
        hub_rel = os.path.relpath(hub_abs, project_root)
    else:
        hub_abs = paths.default_hub_path()
        hub_rel = os.path.relpath(hub_abs, project_root)

    meta = {
        "_comment": _META_COMMENT,
        "legend": dict(DEFAULT_LEGEND),
        "waves": [],
        "closed": [],
        "roster": {},
        "hub": hub_rel,
        # ── headless C&C (bin/fleet spawn/board/resume) ──
        # Per-stage child profiles: model / effort / permission_mode /
        # allowed_tools / max_budget_usd, with "_defaults" as the base
        # layer. CLI flags override the stage profile which overrides
        # "_defaults". All keys optional — an absent key falls through
        # to the claude CLI's own defaults.
        "profiles": {"_defaults": {}},
        # All children draw ONE subscription pool (5-h/weekly limits).
        "max_concurrent": 4,
        # The headless child's prompt; {stage}/{slug} are substituted.
        # "/splock:<stage>" is the installed-plugin spelling — drop the
        # "splock:" prefix for sideloaded/in-tree checkouts.
        "command_template": "/splock:{stage} {slug}",
        # Attended-only stages: `spawn` refuses outright and the PROMPTS
        # zone never renders a runnable line — those slugs queue under
        # the generated ATTENDED zone instead (optional per-slug
        # roster.<slug>.attended {slot, model, effort, ultracode} block).
        "unspawnable_stages": [],
    }
    engine.save_meta(meta)

    if hub is None:
        paths.fleet_dir().mkdir(parents=True, exist_ok=True)
        if not hub_abs.exists():
            _atomic_write_text(hub_abs, _scaffold_hub_text(project_root.name))
    return True, hub_abs


def migrate(
    anchors: dict[str, tuple[str, str]] | None = None,
    *,
    dry_run: bool = False,
) -> str:
    """Wire the `FLEET:*` zones + protocol into the registered hub.

    `anchors` maps zone name → (start_anchor, end_anchor). EVERY anchor
    is verified before anything is transformed; on any miss the full
    missing list is raised and the hub stays byte-identical. Zones not
    anchored are appended in one generated section. The whole
    transformation lands in a single atomic swap.

    Returns a one-line status message. Idempotent: zones whose markers
    are already present are left untouched (their anchors, if given, are
    ignored) — so a hub wired before a zone existed (e.g. pre-PROMPTS)
    is UPGRADED by re-running migrate: only the missing zone lands,
    anchored or appended as a compact block (no duplicate section
    header). All markers present → no-op.
    """
    anchors = anchors or {}
    meta = engine.load_meta()
    hub = paths.hub_path(meta)
    with open(hub, encoding="utf-8") as f:
        text = f.read()

    for zone in anchors:
        if zone not in engine.MARKERS:
            raise ValueError(f"unknown zone {zone!r} (need one of {sorted(engine.MARKERS)})")

    wired = [z for z, (begin, _) in engine.MARKERS.items() if begin in text]
    to_wire = [z for z in engine.MARKERS if z not in wired]
    if not to_wire:
        return "already migrated (markers present) — no-op"
    anchors = {z: a for z, a in anchors.items() if z in to_wire}

    # ── verify EVERY anchor before transforming anything ──────────────
    missing: list[str] = []
    spans: dict[str, tuple[int, int]] = {}
    for zone, (start, end) in anchors.items():
        i = text.find(start)
        if i == -1:
            missing.append(f"{zone}: start {start}")
            continue
        j = text.find(end, i + len(start))
        if j == -1:
            missing.append(f"{zone}: end {end} (after the start anchor)")
            continue
        spans[zone] = (i + len(start), j)
    if missing:
        raise AnchorsMissing(missing)
    ordered = sorted(spans.items(), key=lambda kv: kv[1][0])
    for (za, (_, ja)), (zb, (ib, _)) in zip(ordered, ordered[1:]):
        if ib < ja:
            raise ValueError(
                f"anchor spans for zones {za!r} and {zb!r} overlap — "
                f"pick non-overlapping section anchors"
            )

    # ── transform (in-memory) ─────────────────────────────────────────
    # Replace back-to-front so earlier spans' offsets stay valid.
    for zone, (i, j) in sorted(spans.items(), key=lambda kv: kv[1][0], reverse=True):
        text = text[:i] + "\n\n" + _zone_block(zone) + "\n\n" + text[j:]

    unanchored = [z for z in to_wire if z not in anchors]
    if unanchored:
        if not text.endswith("\n"):
            text += "\n"
        # Fresh migration: one generated section. Upgrade (some zones
        # already wired): compact per-zone blocks — no duplicate header.
        text += "\n" + (_upgrade_blocks(unanchored) if wired
                        else _generated_section(unanchored))

    protocol_added = PROTOCOL_MARKER not in text
    if protocol_added:
        if not text.endswith("\n"):
            text += "\n"
        text += "\n" + PROTOCOL

    if dry_run:
        return f"dry-run: would migrate {hub} ({len(spans)} anchored, {len(unanchored)} appended)"

    _atomic_write_text(hub, text)  # single atomic swap
    zones_label = " / ".join(z.upper() for z in to_wire)
    return (
        f"migrated {hub}: {zones_label} zone{'s' if len(to_wire) != 1 else ''} "
        f"({len(spans)} anchored, {len(unanchored)} appended)"
        + (" + fleet protocol" if protocol_added else "")
    )
