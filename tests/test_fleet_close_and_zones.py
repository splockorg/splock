"""`bin/fleet close` + the TREE/ATTENDED generated zones + E3 deny-list.

Field driver (qum, 2026-07-18 → 07-20): hand-authored copies of derived
hub state rotted twice in one week, and a hand-run closeout left the
hub out of sync within the hour. Pinned here:

- close-verb atomicity: final event + state flip, archive (plain-move
  AND git-mv paths), meta roster→closed[] with dated+waved entry,
  successor mint, one render — plus every pre-mutation refusal and the
  `--no-archive` half-state with its complete-the-close second run;
- board dedupe: a closed-but-live slug renders exactly ONE row;
- E3 `unspawnable_stages`: spawn refuses outright (dry-run included),
  the PROMPTS zone skips deny-listed stages, the ATTENDED zone picks
  them up (with the optional per-slug attended config + slot ordering);
- TREE zone: wave grouping, live rows, collapsed closed rows with
  dates, legacy no-wave closed entries in the trailing group;
- migrate upgrade: a pre-TREE/ATTENDED hub gains ONLY the missing
  zones; an all-wired hub is a no-op.

Run from the splock repo root with the project venv active.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._fleet import close as close_mod  # noqa: E402
from bin._fleet import engine, exit_codes, hub, paths, spawn  # noqa: E402
from bin._fleet.main import main as fleet_main  # noqa: E402

SLUG = "closing_slug"


@pytest.fixture()
def fleet_project(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "adopter"
    (root / "docs" / "plans" / SLUG).mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    hub.init()
    engine.update(SLUG, stage="code", status="done", next_action="closeout",
                  piece="p-closing", wave=2, actor="t")
    meta = engine.load_meta()
    meta["waves"] = [{"id": 2, "title": "Second wave"}]
    meta["roster"] = {SLUG: {"piece": "p-closing", "wave": 2}}
    engine.save_meta(meta)
    return root


# ---------------------------------------------------------------------------
# close: the atomic terminal transition
# ---------------------------------------------------------------------------


def test_full_close_lands_every_half(fleet_project):
    rc = fleet_main(["close", SLUG, "--note", "delivered in full"])
    assert rc == exit_codes.EXIT_OK

    # archive (plain move — tmp project is not a git repo)
    assert not paths.slug_dir(SLUG).exists()
    archived = paths.plans_dir() / "_closed" / SLUG
    assert (archived / "_fleet.json").is_file()
    state = json.loads((archived / "_fleet.json").read_text(encoding="utf-8"))
    assert state["status"] == "closed"
    events = [json.loads(l) for l in
              (archived / "_fleet_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[-1]["status"] == "closed"
    assert events[-1]["note"] == "delivered in full"

    # meta reconcile: roster row moved to a dated, waved closed[] entry
    meta = engine.load_meta()
    assert SLUG not in meta["roster"]
    (entry,) = meta["closed"]
    assert entry["slug"] == SLUG and entry["wave"] == 2
    assert entry["piece"] == "p-closing"
    assert len(entry["closed"]) == 10  # YYYY-MM-DD

    # the one render: hub board shows exactly one (static) closed row,
    # and the TREE collapses the slug under its wave with the date
    hub_text = paths.hub_path(meta).read_text(encoding="utf-8")
    assert hub_text.count(f"`{SLUG}`") >= 1
    assert f"- ✅ `{SLUG}` — closed {entry['closed']}" in hub_text


def test_close_git_mv_path_stages_the_rename(fleet_project):
    root = fleet_project
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t",
                    "-c", "user.email=t@t", "commit", "-qm", "seed"], check=True)
    assert fleet_main(["close", SLUG]) == exit_codes.EXIT_OK
    assert not paths.slug_dir(SLUG).exists()
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert f"docs/plans/_closed/{SLUG}/_fleet.json" in tracked


def test_no_archive_half_state_then_complete(fleet_project, capsys):
    rc = fleet_main(["close", SLUG, "--no-archive", "--note", "shipped, dir stays"])
    assert rc == exit_codes.EXIT_OK
    assert paths.slug_dir(SLUG).is_dir()          # dir stays live
    assert engine.load_state(SLUG)["status"] == "closed"
    meta = engine.load_meta()
    assert SLUG not in meta["roster"] and meta["closed"][0]["slug"] == SLUG

    # renderer holds one-row-per-slug in the half-state: the live closed
    # row wins; the meta closed[] row is suppressed
    board = engine.render_board(engine.load_all_states(), meta)
    assert board.count(f"| `{SLUG}` |") == 1

    # a second close COMPLETES the archive (no refusal, no double meta row)
    assert fleet_main(["close", SLUG]) == exit_codes.EXIT_OK
    assert not paths.slug_dir(SLUG).exists()
    assert (paths.plans_dir() / "_closed" / SLUG).is_dir()
    assert len(engine.load_meta()["closed"]) == 1


def test_close_with_successor_mints_once(fleet_project):
    rc = fleet_main([
        "close", SLUG, "--successor", "next_slug", "--piece", "p-next",
        "--wave", "3", "--next", "/recon",
        "--successor-directive", "carry the deferred components",
    ])
    assert rc == exit_codes.EXIT_OK
    st = engine.load_state("next_slug")
    assert (st["stage"], st["status"], st["next"]) == ("seed", "ready", "/recon")
    assert st["spawn_directive"] == "carry the deferred components"
    assert engine.load_meta()["roster"]["next_slug"] == {"piece": "p-next", "wave": 3}
    # the PROMPTS zone offers the successor's spawn line immediately
    hub_text = paths.hub_path(engine.load_meta()).read_text(encoding="utf-8")
    assert "- `bin/fleet spawn next_slug --stage recon`" in hub_text


@pytest.mark.parametrize("argv,detail", [
    (["close", "no_such_slug"], "unknown slug"),
    (["close", SLUG, "--successor", "s2"], "--successor requires"),
    (["close", SLUG, "--successor", SLUG, "--piece", "p", "--wave", "1",
      "--next", "/recon"], "already exists"),
])
def test_close_refusals_are_premutation(fleet_project, capsys, argv, detail):
    before_meta = paths.meta_path().read_bytes()
    rc = fleet_main(argv)
    assert rc == exit_codes.EXIT_CLOSE_REFUSED
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["error"] == "close_refused" and detail in err["detail"]
    assert paths.meta_path().read_bytes() == before_meta   # nothing mutated
    assert paths.slug_dir(SLUG).is_dir()


def test_close_refuses_already_archived(fleet_project):
    assert fleet_main(["close", SLUG]) == exit_codes.EXIT_OK
    assert fleet_main(["close", SLUG]) == exit_codes.EXIT_CLOSE_REFUSED


# ---------------------------------------------------------------------------
# E3: unspawnable_stages
# ---------------------------------------------------------------------------


@pytest.fixture()
def attended_project(fleet_project) -> Path:
    engine.update(SLUG, stage="implplan", status="ready", next_action="/code",
                  actor="t")
    meta = engine.load_meta()
    meta["unspawnable_stages"] = ["code"]
    meta["roster"][SLUG]["attended"] = {"slot": 2, "model": "claude-fable-5",
                                        "effort": "xhigh", "ultracode": True}
    engine.save_meta(meta)
    return fleet_project


def test_spawn_refuses_denied_stage_even_dry_run(attended_project, capsys):
    for argv in (["spawn", SLUG, "--stage", "code", "--dry-run"],
                 ["spawn", SLUG, "--stage", "code"]):
        rc = fleet_main(argv)
        assert rc == exit_codes.EXIT_SPAWN_REFUSED
        err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
        assert "attended-only" in err["detail"]
        assert f"/splock:code {SLUG}" in err["detail"]


def test_prompts_skips_and_attended_picks_up(attended_project):
    states = engine.load_all_states()
    meta = engine.load_meta()
    prompts = engine.render_prompts(states, meta)
    assert f"spawn {SLUG}" not in prompts       # no runnable line offered
    attended = engine.render_attended(states, meta)
    assert f"`/splock:code {SLUG}`" in attended
    assert "slot: 2 · model: claude-fable-5 · effort: xhigh · ultracode: True" \
        in attended


def test_attended_orders_by_slot_then_wave(attended_project):
    for i, name in enumerate(("zz_later", "aa_unslotted")):
        (paths.plans_dir() / name).mkdir()
        engine.update(name, stage="implplan", status="ready",
                      next_action="/code", actor="t")
    meta = engine.load_meta()
    meta["roster"]["zz_later"] = {"wave": 1, "attended": {"slot": 1}}
    meta["roster"]["aa_unslotted"] = {"wave": 1}
    engine.save_meta(meta)
    attended = engine.render_attended(engine.load_all_states(), meta)
    lines = [l for l in attended.splitlines() if l.startswith("- `/splock:")]
    # slot 1 first, slot 2 second, unslotted (by wave) last
    assert ["zz_later" in lines[0], SLUG in lines[1],
            "aa_unslotted" in lines[2]] == [True, True, True]


def test_attended_empty_states(fleet_project):
    states = engine.load_all_states()
    meta = engine.load_meta()
    assert "No attended-only stages declared" in \
        engine.render_attended(states, meta)
    meta["unspawnable_stages"] = ["code"]
    assert "Nothing queued" in engine.render_attended(states, meta)


# ---------------------------------------------------------------------------
# TREE zone
# ---------------------------------------------------------------------------


def test_tree_groups_waves_collapsed_closed_and_legacy(fleet_project):
    meta = engine.load_meta()
    meta["closed"] = [
        {"slug": "waved_done", "piece": "p", "wave": 2, "closed": "2026-07-19"},
        {"slug": "legacy_done", "piece": "p0", "note": "→ _closed/ (2026-07-12)"},
    ]
    engine.save_meta(meta)
    tree = engine.render_tree(engine.load_all_states(), meta)
    assert "**Wave 2 — Second wave**" in tree
    assert f"`{SLUG}` — closed" not in tree                # live slug ≠ collapsed row
    assert f"`{SLUG}` — code → closeout · p-closing" in tree
    assert "- ✅ `waved_done` — closed 2026-07-19" in tree  # collapsed under wave
    assert "**Closed**" in tree                             # legacy trailing group
    assert "- ✅ `legacy_done` — → _closed/ (2026-07-12)" in tree


def test_tree_no_state_row(fleet_project):
    meta = engine.load_meta()
    meta["roster"]["fresh_slug"] = {"piece": "p-f", "wave": 2}
    engine.save_meta(meta)
    tree = engine.render_tree(engine.load_all_states(), meta)
    assert "- ⏳ `fresh_slug` — (no state yet)" in tree


# ---------------------------------------------------------------------------
# migrate upgrade: only the missing zones land
# ---------------------------------------------------------------------------


def test_migrate_upgrades_pre_tree_hub_with_only_missing_zones(fleet_project):
    hub_file = paths.hub_path(engine.load_meta())
    text = hub_file.read_text(encoding="utf-8")
    for zone in ("tree", "attended"):
        begin, end = engine.MARKERS[zone]
        text = text.replace(begin + "\n", "").replace(end + "\n", "")
        text = text.replace(begin, "").replace(end, "")
    hub_file.write_text(text, encoding="utf-8")

    # pre-upgrade hub still renders (optional-zone contract)
    assert fleet_main(["render", "--write"]) == exit_codes.EXIT_OK

    assert fleet_main(["migrate"]) == exit_codes.EXIT_OK
    upgraded = hub_file.read_text(encoding="utf-8")
    for zone in engine.MARKERS:
        begin, _ = engine.MARKERS[zone]
        assert upgraded.count(begin) == 1, f"{zone} not exactly-once"

    # and a fully-wired hub is a clean no-op
    before = hub_file.read_bytes()
    assert fleet_main(["migrate"]) == exit_codes.EXIT_OK
    assert hub_file.read_bytes() == before