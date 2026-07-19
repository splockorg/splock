"""`bin/fleet init` / `migrate` / `seed` — the one-time adoption path.

The reference (`migrate_launcher.py` + `seed.py`) safety contract,
generalized:

- `migrate` verifies EVERY anchor before a SINGLE atomic swap; on any
  miss it refuses with the full missing list and the hub stays
  byte-identical (no partial migration — ever);
- `migrate` is idempotent (markers present → no-op);
- `seed` is idempotent (existing `_fleet.json` skipped), `--force`
  overwrites, `--events` seeds the timeline, and the meta roster's
  `piece` / `wave` join in.

Run from the splock repo root with the project venv active.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._fleet import engine, exit_codes, hub, paths  # noqa: E402
from bin._fleet.main import main as fleet_main  # noqa: E402

HAND_HUB = """# my launcher

intro narrative, stays hand-editable

## Runnable now

| old | hand | table |
|---|---|---|
| stale | rows | here |

## Status board

| Slug | Status |
|---|---|
| stale | rows |

## History

nothing yet

## Changelog

- day one
"""


@pytest.fixture()
def project(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "adopter"
    (root / "docs" / "plans" / "some_slug").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


@pytest.fixture()
def hand_hub_project(project) -> Path:
    """A project with an existing hand-edited hub, registered via init --hub."""
    hub_md = project / "docs" / "plans" / "launcher.md"
    hub_md.write_text(HAND_HUB, encoding="utf-8")
    assert fleet_main(["init", "--hub", "docs/plans/launcher.md"]) == exit_codes.EXIT_OK
    return hub_md


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_scaffolds_meta_and_hub(project):
    assert fleet_main(["init"]) == exit_codes.EXIT_OK
    meta = json.loads(paths.meta_path().read_text(encoding="utf-8"))
    assert meta["roster"] == {} and meta["waves"] == [] and meta["closed"] == []
    hub_file = paths.hub_path(meta)
    text = hub_file.read_text(encoding="utf-8")
    for begin, end in engine.MARKERS.values():
        assert begin in text and end in text
    assert hub.PROTOCOL_MARKER in text


def test_init_registers_existing_hub_without_scaffolding(hand_hub_project):
    meta = engine.load_meta()
    assert meta["hub"] == "docs/plans/launcher.md"
    assert not paths.default_hub_path().exists()
    # the registered hub is untouched by init itself
    assert hand_hub_project.read_text(encoding="utf-8") == HAND_HUB


def test_init_hub_missing_file_is_usage(project):
    assert fleet_main(["init", "--hub", "docs/plans/nope.md"]) == exit_codes.EXIT_USAGE
    assert not paths.meta_path().exists()


# ---------------------------------------------------------------------------
# migrate
# ---------------------------------------------------------------------------


def test_migrate_anchored_zones_single_swap(hand_hub_project):
    rc = fleet_main([
        "migrate",
        "--now-start", "## Runnable now", "--now-end", "## Status board",
        "--board-start", "## Status board", "--board-end", "## History",
        "--recent-start", "## History", "--recent-end", "## Changelog",
    ])
    assert rc == exit_codes.EXIT_OK
    text = hand_hub_project.read_text(encoding="utf-8")
    # anchors preserved, marker zones between them, stale tables gone
    for anchor in ("## Runnable now", "## Status board", "## History", "## Changelog"):
        assert anchor in text
    for begin, end in engine.MARKERS.values():
        assert begin in text and end in text
    assert "| stale | rows | here |" not in text
    assert "| stale | rows |" not in text
    assert "intro narrative, stays hand-editable" in text
    assert "- day one" in text
    assert hub.PROTOCOL_MARKER in text

    # ...and the wired hub renders
    engine.update("some_slug", stage="recon", status="ready",
                  next_action="/qa", actor="t")
    assert fleet_main(["render", "--write"]) == exit_codes.EXIT_OK
    assert "| `some_slug` |" in hand_hub_project.read_text(encoding="utf-8")


def test_migrate_missing_anchor_refuses_byte_identical(hand_hub_project, capsys):
    before = hand_hub_project.read_bytes()
    rc = fleet_main([
        "migrate",
        "--now-start", "## Runnable now", "--now-end", "## NOT THERE",
        "--board-start", "## ALSO NOT THERE", "--board-end", "## History",
    ])
    assert rc == exit_codes.EXIT_HUB_ANCHOR_MISSING
    assert hand_hub_project.read_bytes() == before  # no partial migration
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["error"] == "hub_anchor_missing"
    assert len(err["missing"]) == 2  # EVERY missing anchor reported at once


def test_migrate_unanchored_zones_are_appended(hand_hub_project):
    assert fleet_main([
        "migrate",
        "--board-start", "## Status board", "--board-end", "## History",
    ]) == exit_codes.EXIT_OK
    text = hand_hub_project.read_text(encoding="utf-8")
    assert hub.SECTION_MARKER in text  # now + recent landed in the appendix
    assert text.count("FLEET:NOW:BEGIN") == 1
    assert text.count("FLEET:BOARD:BEGIN") == 1


def _strip_prompts_zone(hub_md: Path) -> None:
    """Rebuild the hub as a pre-PROMPTS wiring (three zones, protocol)."""
    text = hub_md.read_text(encoding="utf-8")
    begin, end = engine.MARKERS["prompts"]
    keep = [ln for ln in text.splitlines()
            if ln not in (begin, end, hub._ZONE_HEADINGS["prompts"])]
    hub_md.write_text("\n".join(keep) + "\n", encoding="utf-8")


def test_migrate_upgrades_a_pre_prompts_hub(hand_hub_project, capsys):
    """A hub migrated before the PROMPTS zone existed gains ONLY that zone
    on re-run — no duplicate section header or protocol, wired zones
    untouched."""
    assert fleet_main(["migrate"]) == exit_codes.EXIT_OK
    _strip_prompts_zone(hand_hub_project)
    before = hand_hub_project.read_text(encoding="utf-8")
    assert "FLEET:PROMPTS" not in before

    assert fleet_main(["migrate"]) == exit_codes.EXIT_OK
    assert "PROMPTS zone (0 anchored, 1 appended)" in capsys.readouterr().out
    text = hand_hub_project.read_text(encoding="utf-8")
    for begin, end in engine.MARKERS.values():
        assert text.count(begin) == 1 and text.count(end) == 1
    assert text.count(hub.SECTION_MARKER) == 1  # no duplicated header
    assert text.count(hub.PROTOCOL_MARKER) == 1
    # …and the upgraded hub renders all four zones
    engine.update("some_slug", stage="qa", status="ready",
                  next_action="/plan", actor="t")
    assert fleet_main(["render", "--write"]) == exit_codes.EXIT_OK
    assert ("- `bin/fleet spawn some_slug --stage plan`"
            in hand_hub_project.read_text(encoding="utf-8"))

    # a third migrate is a full no-op again
    before = hand_hub_project.read_bytes()
    assert fleet_main(["migrate"]) == exit_codes.EXIT_OK
    assert hand_hub_project.read_bytes() == before


def test_migrate_upgrade_honors_prompts_anchors(hand_hub_project):
    assert fleet_main([
        "migrate",
        "--now-start", "## Runnable now", "--now-end", "## Status board",
        "--board-start", "## Status board", "--board-end", "## History",
        "--recent-start", "## History", "--recent-end", "## Changelog",
    ]) == exit_codes.EXIT_OK
    _strip_prompts_zone(hand_hub_project)

    # the operator anchors the new zone into a marker-free span; re-running
    # with the ORIGINAL anchor set for the wired zones stays safe (ignored)
    assert fleet_main([
        "migrate",
        "--now-start", "## Runnable now", "--now-end", "## Status board",
        "--prompts-start", "## Changelog", "--prompts-end", "- day one",
    ]) == exit_codes.EXIT_OK
    text = hand_hub_project.read_text(encoding="utf-8")
    begin, _ = engine.MARKERS["prompts"]
    assert text.count(begin) == 1
    assert text.find("## Changelog") < text.find(begin) < text.find("- day one")
    assert text.count(engine.MARKERS["now"][0]) == 1  # wired zone untouched


def test_migrate_is_idempotent(hand_hub_project, capsys):
    assert fleet_main(["migrate"]) == exit_codes.EXIT_OK
    after_first = hand_hub_project.read_bytes()
    assert fleet_main(["migrate"]) == exit_codes.EXIT_OK
    assert "already migrated" in capsys.readouterr().out
    assert hand_hub_project.read_bytes() == after_first


def test_migrate_dry_run_writes_nothing(hand_hub_project):
    before = hand_hub_project.read_bytes()
    assert fleet_main(["migrate", "--dry-run"]) == exit_codes.EXIT_OK
    assert hand_hub_project.read_bytes() == before


def test_migrate_overlapping_anchors_is_usage(hand_hub_project):
    before = hand_hub_project.read_bytes()
    rc = fleet_main([
        "migrate",
        # now-span runs past the board-span start → overlap
        "--now-start", "## Runnable now", "--now-end", "## History",
        "--board-start", "## Status board", "--board-end", "## Changelog",
    ])
    assert rc == exit_codes.EXIT_USAGE
    assert hand_hub_project.read_bytes() == before


def test_migrate_start_without_end_is_usage(hand_hub_project):
    assert fleet_main(
        ["migrate", "--now-start", "## Runnable now"]
    ) == exit_codes.EXIT_USAGE


def test_migrate_requires_init(project, tmp_path):
    assert fleet_main(["migrate"]) == exit_codes.EXIT_FLEET_NOT_INITIALIZED


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------

SEED_DOC = {
    "as_of": "2026-07-18T00:00:00Z",
    "states": {
        "some_slug": {"stage": "recon", "status": "ready", "next": "/qa",
                      "blockers": ""},
        "other_slug": {"stage": "test+review", "status": "done",
                       "next": "closeout", "blockers": "drift-guard pin"},
    },
    "events": [
        {"ts": "2026-07-18T01:00:00Z", "slug": "some_slug", "stage": "recon",
         "status": "done", "actor": "recon-agent", "note": "recon authored"},
    ],
}


@pytest.fixture()
def seeded_ready(project, tmp_path) -> Path:
    assert fleet_main(["init"]) == exit_codes.EXIT_OK
    meta = engine.load_meta()
    meta["roster"] = {"some_slug": {"piece": "piece-A", "wave": 5}}
    engine.save_meta(meta)
    doc = tmp_path / "seed.json"
    doc.write_text(json.dumps(SEED_DOC), encoding="utf-8")
    return doc


def test_seed_writes_and_joins_roster(seeded_ready):
    assert fleet_main(["seed", "--from", str(seeded_ready)]) == exit_codes.EXIT_OK
    state = engine.load_state("some_slug")
    assert state["piece"] == "piece-A" and state["wave"] == 5
    assert state["updated"] == "2026-07-18T00:00:00Z"
    assert state["actor"] == "fleet-seed"
    assert engine.load_state("other_slug")["blockers"] == "drift-guard pin"
    # --events not passed → no timeline seeded
    assert not paths.log_path("some_slug").exists()


def test_seed_is_idempotent_and_force_overwrites(seeded_ready, capsys):
    assert fleet_main(["seed", "--from", str(seeded_ready)]) == exit_codes.EXIT_OK
    engine.update("some_slug", status="wip", actor="live-agent")
    # a second seed skips the live slug…
    assert fleet_main(["seed", "--from", str(seeded_ready)]) == exit_codes.EXIT_OK
    assert "wrote 0, skipped 2" in capsys.readouterr().out
    assert engine.load_state("some_slug")["status"] == "wip"
    # …and --force restores the seeded state
    assert fleet_main(["seed", "--from", str(seeded_ready), "--force"]) == exit_codes.EXIT_OK
    assert engine.load_state("some_slug")["status"] == "ready"


def test_seed_events_flag_appends_timeline(seeded_ready):
    assert fleet_main(["seed", "--from", str(seeded_ready), "--events"]) == exit_codes.EXIT_OK
    events = engine.load_all_events()
    assert any(e["note"] == "recon authored" for e in events)


def test_seed_rejects_bad_status(project, tmp_path):
    assert fleet_main(["init"]) == exit_codes.EXIT_OK
    doc = tmp_path / "bad.json"
    doc.write_text(json.dumps({"states": {"s": {"status": "nope"}}}),
                   encoding="utf-8")
    assert fleet_main(["seed", "--from", str(doc)]) == exit_codes.EXIT_USAGE
    assert engine.load_state("s") is None
