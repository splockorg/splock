"""`bin/fleet` engine contract — update / state / render + safety properties.

Ports the behavioral guarantees of the qum reference implementation
(`scripts/fleet/fleet.py`) into pytest:

- per-slug state roundtrip; atomic swap leaves no `.tmp` residue;
- append-only event log; note clamp keeps every line < PIPE_BUF;
- torn-line tolerance — a partial/garbage log line never corrupts the
  fold;
- render projection: Now / board / recent bodies, closed-from-meta
  rows, actionable-first ordering, pipe escaping;
- `render --write` touches ONLY the marker zones (hand-authored
  narrative survives byte-identical) and refuses byte-untouched when
  markers are missing;
- the CLI's opt-in gate (`update` and BOTH render modes refuse with
  exit 45 until `bin/fleet init` — a wrong-cwd render must never
  silently claim an empty fleet; first-field-deployment defect,
  2026-07-19).

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

SLUG = "fleet_test_slug"


@pytest.fixture()
def project(tmp_path, monkeypatch) -> Path:
    """A tmp adopter project with one slug dir; fleet NOT yet initialized."""
    root = tmp_path / "adopter"
    (root / "docs" / "plans" / SLUG).mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


@pytest.fixture()
def fleet_project(project) -> Path:
    hub.init()
    return project


# ---------------------------------------------------------------------------
# opt-in gate
# ---------------------------------------------------------------------------


def test_update_refuses_until_init(project, capsys):
    rc = fleet_main(["update", SLUG, "--status", "ready"])
    assert rc == exit_codes.EXIT_FLEET_NOT_INITIALIZED
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["error"] == "fleet_not_initialized"
    assert not paths.state_path(SLUG).exists()


def test_init_is_idempotent(fleet_project, capsys):
    assert paths.meta_path().is_file()
    hub_file = paths.hub_path(engine.load_meta())
    before = hub_file.read_bytes()
    assert fleet_main(["init"]) == exit_codes.EXIT_OK
    assert "already initialized" in capsys.readouterr().out
    assert hub_file.read_bytes() == before


# ---------------------------------------------------------------------------
# update / state
# ---------------------------------------------------------------------------


def test_update_roundtrip_and_event(fleet_project):
    rc = fleet_main([
        "update", SLUG, "--stage", "recon", "--status", "ready",
        "--next", "/qa", "--actor", "recon-agent", "--note", "recon authored",
    ])
    assert rc == exit_codes.EXIT_OK
    state = json.loads(paths.state_path(SLUG).read_text(encoding="utf-8"))
    assert state["stage"] == "recon"
    assert state["status"] == "ready"
    assert state["next"] == "/qa"
    assert state["updated"].endswith("Z")

    events = [json.loads(l) for l in
              paths.log_path(SLUG).read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    assert events[0]["note"] == "recon authored"
    assert events[0]["actor"] == "recon-agent"

    # partial update merges into existing state
    assert fleet_main(["update", SLUG, "--status", "wip", "--actor", "qa-agent"]) == 0
    state = engine.load_state(SLUG)
    assert state["status"] == "wip"
    assert state["stage"] == "recon"  # untouched field survives


def test_update_rejects_unknown_status(fleet_project, capsys):
    rc = fleet_main(["update", SLUG, "--status", "in-progress"])
    assert rc == exit_codes.EXIT_USAGE
    assert not paths.state_path(SLUG).exists()


def test_update_with_no_status_on_fresh_slug_is_usage(fleet_project):
    # the merged state has no valid status → usage, matching the reference
    assert fleet_main(["update", SLUG, "--stage", "recon"]) == exit_codes.EXIT_USAGE


def test_atomic_swap_leaves_no_tmp_residue(fleet_project):
    engine.update(SLUG, status="ready", actor="t")
    residue = list(paths.slug_dir(SLUG).glob("*.tmp"))
    assert residue == []


def test_state_subcommand(fleet_project, capsys):
    assert fleet_main(["state", SLUG]) == exit_codes.EXIT_OK
    assert f"(no state for {SLUG})" in capsys.readouterr().out
    engine.update(SLUG, stage="qa", status="wip", actor="t")
    assert fleet_main(["state", SLUG]) == exit_codes.EXIT_OK
    assert json.loads(capsys.readouterr().out)["stage"] == "qa"


# ---------------------------------------------------------------------------
# append-only log: clamp + torn-line tolerance
# ---------------------------------------------------------------------------


def test_note_clamp_keeps_line_under_pipe_buf(fleet_project):
    engine.update(SLUG, status="ready", actor="t", note="x" * 5000)
    (line,) = paths.log_path(SLUG).read_text(encoding="utf-8").splitlines()
    assert len(line.encode("utf-8")) < 4096
    assert json.loads(line)["note"].endswith("…")


def test_torn_line_never_corrupts_the_fold(fleet_project):
    engine.update(SLUG, status="ready", actor="t", note="first")
    # simulate a torn append: an unterminated partial JSON line, then a
    # valid append from another writer
    with open(paths.log_path(SLUG), "a", encoding="utf-8") as f:
        f.write('{"ts": "2026-07-18T00:00:00Z", "slug": "torn\n')
        f.write("\n")  # blank line is skipped too
    engine.update(SLUG, status="wip", actor="t", note="second")
    events = engine.load_all_events()
    notes = [e["note"] for e in events]
    assert notes == ["first", "second"]


# ---------------------------------------------------------------------------
# render projection
# ---------------------------------------------------------------------------


def _mk_slug(name: str, **fields):
    (paths.plans_dir() / name).mkdir(exist_ok=True)
    engine.update(name, actor="t", **fields)


def test_render_bodies(fleet_project):
    _mk_slug("alpha", stage="recon", status="ready", next_action="/qa")
    _mk_slug("beta", stage="code", status="wip", next_action="/code")
    _mk_slug("gamma", stage="test", status="blocked", blockers="cap exhausted",
             note="a|b")  # pipe must be escaped in the recent zone
    meta = engine.load_meta()
    meta["closed"] = [{"slug": "old_one", "piece": "p0", "note": "archived"}]
    engine.save_meta(meta)

    zones = engine.render_zones()
    now_lines = zones["now"].splitlines()
    # actionable only, wip first
    assert "`beta`" in now_lines[2]
    assert "`alpha`" in now_lines[3]
    assert "gamma" not in zones["now"]
    # board: every live slug + the static closed row from meta
    assert "`gamma`" in zones["board"]
    assert "| `old_one` | p0 | — | — | ✅ closed | archived |" in zones["board"]
    # recent: newest first, pipe escaped
    assert "a\\|b" in zones["recent"]


def test_render_write_touches_only_the_zones(fleet_project):
    hub_file = paths.hub_path(engine.load_meta())
    narrative = "\n## Hand-authored notes\n\nkeep me byte-identical\n"
    hub_file.write_text(hub_file.read_text(encoding="utf-8") + narrative,
                        encoding="utf-8")
    _mk_slug("alpha", stage="recon", status="ready", next_action="/qa")

    assert fleet_main(["render", "--write"]) == exit_codes.EXIT_OK
    text = hub_file.read_text(encoding="utf-8")
    assert "keep me byte-identical" in text
    assert "| `alpha` |" in text

    # idempotent: a second render with unchanged state is byte-stable
    before = hub_file.read_bytes()
    assert fleet_main(["render", "--write"]) == exit_codes.EXIT_OK
    assert hub_file.read_bytes() == before


def test_spawn_directive_roundtrips_survives_merges_and_clears(fleet_project):
    rc = fleet_main([
        "update", SLUG, "--stage", "qa", "--status", "ready",
        "--next", "/plan", "--actor", "op",
        "--spawn-directive", "OPERATOR DIRECTIVES: ingest the qa recs in Call 1.",
    ])
    assert rc == exit_codes.EXIT_OK
    state = json.loads(paths.state_path(SLUG).read_text(encoding="utf-8"))
    assert state["spawn_directive"].startswith("OPERATOR DIRECTIVES")

    # a partial update from another actor leaves the directive alone
    assert fleet_main(["update", SLUG, "--status", "wip", "--actor", "t"]) == 0
    assert engine.load_state(SLUG)["spawn_directive"].startswith("OPERATOR")

    # "" clears by hand
    assert fleet_main(["update", SLUG, "--spawn-directive", "", "--actor", "t"]) == 0
    assert engine.load_state(SLUG)["spawn_directive"] == ""


def test_render_prompts_ready_held_and_closed_dropped(fleet_project):
    _mk_slug("alpha", stage="qa", status="ready", next_action="/plan",
             spawn_directive="ingest the qa recs;\n  plan against the current tree")
    _mk_slug("beta", stage="test", status="done", next_action="closeout")
    _mk_slug("gamma", stage="test", status="blocked", blockers="cap exhausted")
    _mk_slug("delta", stage="code", status="wip", next_action="/code")
    _mk_slug("omega", stage="closeout", status="closed", next_action="—")
    _mk_slug("sigma", stage="recon", status="ready", next_action="/splock:qa")

    body = engine.render_zones()["prompts"]
    # ready → runnable one-liners; the stored directive is an annotation,
    # never part of the command (spawn applies it at spawn time)
    assert "- `bin/fleet spawn alpha --stage plan`" in body
    assert "- `bin/fleet spawn sigma --stage qa`" in body  # /splock: spelling
    assert "--prompt-suffix" not in body
    # multi-line directive collapses to one display line
    assert "  - directive: ingest the qa recs; plan against the current tree" in body
    # held group carries the blockers line
    assert "- `gamma` — ❌ blocked: cap exhausted" in body
    # wip / done / closed slugs never appear — closeout can't leave husks
    for absent in ("beta", "delta", "omega"):
        assert absent not in body


def test_render_prompts_unspawnable_next_and_empty_placeholder(fleet_project):
    assert "_Nothing to spawn" in engine.render_zones()["prompts"]
    _mk_slug("alpha", stage="test", status="ready", next_action="operator ruling")
    body = engine.render_zones()["prompts"]
    assert "- `alpha` — next: operator ruling (not a stage command" in body
    assert "bin/fleet spawn alpha" not in body


def test_render_prompts_directive_display_clamped(fleet_project):
    _mk_slug("alpha", stage="qa", status="ready", next_action="/plan",
             spawn_directive="x" * 5000)
    (line,) = [l for l in engine.render_zones()["prompts"].splitlines()
               if l.startswith("  - directive:")]
    assert line.endswith("…")
    assert len(line) < engine.MAX_DIRECTIVE_DISPLAY + 20
    # the STATE keeps the full text — only the display is clamped
    assert len(engine.load_state("alpha")["spawn_directive"]) == 5000


def test_render_write_skips_prompts_zone_on_legacy_hub(fleet_project):
    """A hub wired before the PROMPTS zone existed keeps working unchanged."""
    hub_file = paths.hub_path(engine.load_meta())
    legacy = "\n".join(
        ["# legacy three-zone hub", ""]
        + [f"{b}\n{e}" for z, (b, e) in engine.MARKERS.items() if z != "prompts"]
    ) + "\n"
    hub_file.write_text(legacy, encoding="utf-8")
    _mk_slug("alpha", stage="recon", status="ready", next_action="/qa")

    assert fleet_main(["render", "--write"]) == exit_codes.EXIT_OK
    text = hub_file.read_text(encoding="utf-8")
    assert "| `alpha` |" in text            # legacy zones rendered
    assert "FLEET:PROMPTS" not in text      # optional zone silently skipped


def test_render_write_refuses_when_markers_missing(fleet_project, capsys):
    hub_file = paths.hub_path(engine.load_meta())
    hub_file.write_text("# a hub with no markers\n", encoding="utf-8")
    before = hub_file.read_bytes()
    rc = fleet_main(["render", "--write"])
    assert rc == exit_codes.EXIT_HUB_ANCHOR_MISSING
    assert hub_file.read_bytes() == before  # byte-untouched on refusal
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["error"] == "hub_markers_missing"


def test_render_refuses_uninitialized_in_both_modes(project, monkeypatch,
                                                    capsys):
    """Wrong-cwd render must refuse loudly, never claim an empty fleet.

    Field defect (first deployment, 2026-07-19): print-mode render from
    a non-fleet directory exited 0 with '_Nothing active…_' zones —
    silent success with wrong data. Both modes now share `spawn`'s
    refuse-cleanly posture, and the error names the invoking dir so the
    operator sees WHERE the lookup started.
    """
    monkeypatch.setenv("SPLOCK_CALLER_PWD", "/definitely/not/a/fleet/repo")
    for argv in (["render"], ["render", "--write"]):
        rc = fleet_main(argv)
        assert rc == exit_codes.EXIT_FLEET_NOT_INITIALIZED
        captured = capsys.readouterr()
        assert "Nothing active" not in captured.out  # no empty-universe zones
        err = json.loads(captured.err.strip().splitlines()[-1])
        assert err["error"] == "fleet_not_initialized"
        assert "/definitely/not/a/fleet/repo" in err["detail"]


def test_render_print_works_once_initialized(fleet_project, capsys):
    assert fleet_main(["render"]) == exit_codes.EXIT_OK
    assert "===== now =====" in capsys.readouterr().out
