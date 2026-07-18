"""Fleet headless C&C — spawn / board / resume, without spawning anything real.

Every subprocess surface is DI'd or monkeypatched: the runner's claude
invocation is a fake callable, the spawner's detached-runner launch is a
recorded stub. What stays real: the per-slug `_fleet_runs.jsonl` ledger
discipline, profile resolution, argv assembly (the CLI-subprocess
transport contract), refusal exit codes, the board fold, and the
`bin/wrap` directive envelope.

Platform facts these tests encode were verified live on 2026-07-18
against Claude Code CLI 2.1.214 (see docs/FLEET.md §Headless C&C):
`--output-format json` yields `session_id` / `total_cost_usd` /
`permission_denials`; `--effort low..max`, `--permission-mode`,
`--allowedTools`, `--max-budget-usd` exist per-invocation; headless
`--resume <sid>` re-enters with context intact; subscription OAuth works
with no `ANTHROPIC_API_KEY`.

Run from the splock repo root with the project venv active.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin._fleet import (  # noqa: E402
    board,
    engine,
    exit_codes,
    hub,
    paths,
    runs,
    spawn,
    spawn_runner,
)
from bin._fleet.main import main as fleet_main  # noqa: E402

SLUG = "cnc_slug"
DEAD_PID = 2 ** 22 + 12345  # beyond any live pid on the test host


@pytest.fixture()
def fleet_project(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "adopter"
    (root / "docs" / "plans" / SLUG).mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    hub.init()
    return root


def _start_row(run_id: str, *, pid: int, event: str = "spawned",
               session: str | None = None) -> dict:
    row = {"ts": "2026-07-18T00:00:00Z", "run_id": run_id, "slug": SLUG,
           "stage": "qa", "event": event, "pid": pid,
           "model": "m", "effort": None, "permission_mode": None}
    if session:
        row["session_id"] = session
    return row


def _end_row(run_id: str, *, session: str = "sid-1", cost: float = 0.01,
             is_error: bool = False, denials: int = 0,
             event: str = "completed") -> dict:
    return {"ts": "2026-07-18T00:01:00Z", "run_id": run_id, "slug": SLUG,
            "stage": "qa", "event": event, "exit_code": 0,
            "session_id": session, "total_cost_usd": cost,
            "is_error": is_error, "subtype": "success", "num_turns": 3,
            "denials": denials, "result_snippet": "ok"}


# ---------------------------------------------------------------------------
# runs ledger
# ---------------------------------------------------------------------------


def test_ledger_roundtrip_snippet_clamp_and_torn_tolerance(fleet_project):
    runs.append_run(SLUG, _start_row("r1", pid=os.getpid()))
    with open(runs.runs_path(SLUG), "a", encoding="utf-8") as f:
        f.write('{"torn": "half a li')  # torn append, no newline
        f.write("\n")
    runs.append_run(SLUG, _end_row("r1", session="sid-abc"))
    runs.append_run(SLUG, {**_end_row("r2"), "result_snippet": "y" * 2000})

    rows = runs.load_runs(SLUG)
    assert [r.get("run_id") for r in rows] == ["r1", "r1", "r2"]  # torn skipped
    assert rows[-1]["result_snippet"].endswith("…")
    assert len(rows[-1]["result_snippet"]) <= runs.MAX_SNIPPET_CHARS
    # every stored line stays < PIPE_BUF
    for ln in runs.runs_path(SLUG).read_text(encoding="utf-8").splitlines():
        assert len(ln.encode("utf-8")) < 4096


def test_latest_session_prefers_newest_and_sees_resume_rows(fleet_project):
    assert runs.latest_session_id(SLUG) is None
    runs.append_run(SLUG, _end_row("r1", session="sid-old"))
    runs.append_run(SLUG, _start_row("r2", pid=DEAD_PID, event="resumed",
                                     session="sid-new"))
    assert runs.latest_session_id(SLUG) == "sid-new"


def test_split_runs_liveness(fleet_project):
    runs.append_run(SLUG, _start_row("live", pid=os.getpid()))
    runs.append_run(SLUG, _start_row("dead", pid=DEAD_PID))
    runs.append_run(SLUG, _start_row("done", pid=DEAD_PID))
    runs.append_run(SLUG, _end_row("done"))
    live, died, ended = runs.split_runs(runs.load_runs(SLUG))
    assert [r["run_id"] for r in live] == ["live"]
    assert [r["run_id"] for r in died] == ["dead"]
    assert [r["run_id"] for r in ended] == ["done"]
    assert runs.live_run_count() == 1


# ---------------------------------------------------------------------------
# profile resolution + argv assembly (the transport contract)
# ---------------------------------------------------------------------------


def test_profile_precedence_cli_over_stage_over_defaults():
    meta = {
        "profiles": {
            "_defaults": {"model": "m-default", "permission_mode": "default"},
            "code": {"model": "m-code", "effort": "xhigh",
                     "allowed_tools": ["Bash", "Edit"]},
        },
        "max_concurrent": 2,
        "command_template": "/splock:{stage} {slug}",
    }
    p = spawn.resolve_profile(meta, "code", {"model": "m-cli"})
    assert p["model"] == "m-cli"                 # CLI wins
    assert p["effort"] == "xhigh"                # stage layer
    assert p["permission_mode"] == "default"     # _defaults layer
    assert p["allowed_tools"] == ["Bash", "Edit"]
    assert p["max_concurrent"] == 2
    # unknown stage → only _defaults
    p2 = spawn.resolve_profile(meta, "recon", {})
    assert p2["model"] == "m-default" and p2["effort"] is None


def test_child_argv_is_the_cli_transport():
    profile = {"model": "claude-fable-5", "effort": "xhigh",
               "permission_mode": "acceptEdits",
               "allowed_tools": ["Bash(git diff *)", "Read"],
               "max_budget_usd": "2.50"}
    prompt = spawn.build_prompt("/splock:{stage} {slug}", "code", "s1", "extra")
    argv = spawn.build_child_argv(prompt, profile)
    assert argv[0:2] == ["claude", "-p"]         # CLI subprocess, never SDK
    assert argv[2] == "/splock:code s1\n\nextra"
    assert argv[3:5] == ["--output-format", "json"]
    for flag, val in (("--model", "claude-fable-5"), ("--effort", "xhigh"),
                      ("--permission-mode", "acceptEdits"),
                      ("--max-budget-usd", "2.50")):
        assert argv[argv.index(flag) + 1] == val
    i = argv.index("--allowedTools")
    assert argv[i + 1:i + 3] == ["Bash(git diff *)", "Read"]


def test_child_argv_resume_and_minimal():
    argv = spawn.build_child_argv("go on", {}, resume_session="sid-9")
    assert argv[:5] == ["claude", "-p", "--resume", "sid-9", "go on"]
    assert "--model" not in argv  # omitted → session/CLI defaults


# ---------------------------------------------------------------------------
# spawn: refusals + the launch path
# ---------------------------------------------------------------------------


def test_spawn_cli_requires_init(tmp_path, monkeypatch, capsys):
    root = tmp_path / "adopter"
    (root / "docs" / "plans" / SLUG).mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    rc = fleet_main(["spawn", SLUG, "--stage", "qa"])
    assert rc == exit_codes.EXIT_FLEET_NOT_INITIALIZED


def test_spawn_refuses_missing_slug_dir(fleet_project):
    rc = fleet_main(["spawn", "no_such_slug", "--stage", "qa"])
    assert rc == exit_codes.EXIT_SPAWN_REFUSED


def test_spawn_refuses_without_claude_cli(fleet_project, monkeypatch):
    monkeypatch.setattr(spawn.shutil, "which", lambda _: None)
    rc = fleet_main(["spawn", SLUG, "--stage", "qa"])
    assert rc == exit_codes.EXIT_SPAWN_REFUSED
    assert runs.load_runs(SLUG) == []  # nothing recorded on refusal


def test_spawn_enforces_max_concurrent(fleet_project, monkeypatch, capsys):
    meta = engine.load_meta()
    meta["max_concurrent"] = 1
    engine.save_meta(meta)
    runs.append_run(SLUG, _start_row("busy", pid=os.getpid()))  # one live child
    monkeypatch.setattr(spawn.shutil, "which", lambda _: "/usr/bin/claude")
    rc = fleet_main(["spawn", SLUG, "--stage", "qa"])
    assert rc == exit_codes.EXIT_SPAWN_REFUSED
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert "max_concurrent" in err["detail"]


def test_spawn_dry_run_prints_argv_and_launches_nothing(fleet_project, capsys):
    rc = fleet_main(["spawn", SLUG, "--stage", "recon",
                     "--model", "claude-opus-4-8", "--dry-run"])
    assert rc == exit_codes.EXIT_OK
    out = capsys.readouterr().out
    assert f"'/splock:recon {SLUG}'" in out and "--model claude-opus-4-8" in out
    assert runs.load_runs(SLUG) == []


def test_spawn_consumes_stored_directive_cli_overrides(fleet_project):
    """The stored spawn_directive is the default child-prompt suffix —
    resolved at SPAWN time, so no rendered/pasted command can carry a
    stale copy. An explicit --prompt-suffix (even "") overrides it."""
    engine.update(SLUG, stage="qa", status="ready", next_action="/plan",
                  actor="op", spawn_directive="OPERATOR DIRECTIVES: plan tight.")

    _, argv = spawn.spawn(SLUG, "plan", overrides={}, dry_run=True)
    assert argv[2] == f"/splock:plan {SLUG}\n\nOPERATOR DIRECTIVES: plan tight."

    _, argv = spawn.spawn(SLUG, "plan", overrides={},
                          prompt_suffix="use THIS instead", dry_run=True)
    assert argv[2].endswith("\n\nuse THIS instead")

    _, argv = spawn.spawn(SLUG, "plan", overrides={}, prompt_suffix="",
                          dry_run=True)
    assert argv[2] == f"/splock:plan {SLUG}"  # "" = explicitly bare


def test_board_json_surfaces_spawn_directive(fleet_project, capsys):
    engine.update(SLUG, stage="qa", status="ready", next_action="/plan",
                  actor="op", spawn_directive="ingest the qa recs")
    assert fleet_main(["board", "--json"]) == exit_codes.EXIT_OK
    b = json.loads(capsys.readouterr().out)
    assert b["slugs"][SLUG]["spawn_directive"] == "ingest the qa recs"


def test_spawn_records_ledger_row_and_payload(fleet_project, monkeypatch):
    monkeypatch.setattr(spawn.shutil, "which", lambda _: "/usr/bin/claude")
    launched: dict = {}

    def fake_launcher(payload):
        launched.update(payload)
        return 4242

    run_id, argv = spawn.spawn(
        SLUG, "qa", overrides={"model": "m-x"}, launcher=fake_launcher,
    )
    (row,) = runs.load_runs(SLUG)
    assert row["event"] == "spawned" and row["pid"] == 4242
    assert row["run_id"] == run_id and row["model"] == "m-x"
    assert launched["argv"] == argv
    assert launched["slug"] == SLUG
    assert launched["project_root"] == str(fleet_project)
    assert Path(launched["out_json_path"]).parent == spawn.runs_artifact_dir()


# ---------------------------------------------------------------------------
# the detached runner
# ---------------------------------------------------------------------------


def _payload(fleet_project, run_id: str = "r-run") -> dict:
    art = spawn.runs_artifact_dir()
    art.mkdir(parents=True, exist_ok=True)
    return {
        "run_id": run_id, "slug": SLUG, "stage": "qa",
        "argv": ["claude", "-p", "x", "--output-format", "json"],
        "project_root": str(fleet_project),
        "out_json_path": str(art / f"{run_id}.json"),
        "log_path": str(art / f"{run_id}.log"),
    }


def test_runner_success_appends_completion_and_renders(fleet_project):
    child_json = json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "result": "DONE", "session_id": "sid-run", "total_cost_usd": 0.05,
        "num_turns": 4,
        "permission_denials": [{"tool_name": "Write"}],
    })
    payload = _payload(fleet_project)
    rc = spawn_runner.run(payload, claude_runner=lambda a, c, l: (0, child_json))
    assert rc == 0
    (row,) = runs.load_runs(SLUG)
    assert (row["event"], row["session_id"], row["total_cost_usd"],
            row["denials"], row["result_snippet"]) == \
        ("completed", "sid-run", 0.05, 1, "DONE")
    assert json.loads(Path(payload["out_json_path"]).read_text())["result"] == "DONE"
    # the runner re-rendered the hub as its final act
    hub_text = paths.hub_path(engine.load_meta()).read_text(encoding="utf-8")
    assert "sid-run" not in hub_text  # runs stay off the qum-compatible zones


def test_runner_unparseable_stdout_degrades_to_failed_row(fleet_project):
    rc = spawn_runner.run(_payload(fleet_project),
                          claude_runner=lambda a, c, l: (7, "not json at all"))
    assert rc == 1
    (row,) = runs.load_runs(SLUG)
    assert row["event"] == "failed" and row["exit_code"] == 7
    assert "not json" in row["result_snippet"]


def test_runner_spawn_crash_degrades_to_failed_row(fleet_project):
    def exploding_runner(a, c, l):
        raise OSError("no such binary")

    rc = spawn_runner.run(_payload(fleet_project), claude_runner=exploding_runner)
    assert rc == 1
    (row,) = runs.load_runs(SLUG)
    assert row["event"] == "failed" and "no such binary" in row["result_snippet"]


# ---------------------------------------------------------------------------
# board
# ---------------------------------------------------------------------------


def test_board_folds_states_runs_cost_and_attention(fleet_project, capsys):
    engine.update(SLUG, stage="test", status="blocked",
                  blockers="cap exhausted", actor="retry-loop")
    other = "smooth_slug"
    (paths.plans_dir() / other).mkdir()
    engine.update(other, stage="recon", status="ready", next_action="/qa",
                  actor="t")

    runs.append_run(SLUG, _start_row("r1", pid=DEAD_PID))
    runs.append_run(SLUG, _end_row("r1", session="sid-blk", cost=0.30, denials=2))
    runs.append_run(SLUG, _start_row("r2", pid=os.getpid()))      # live child
    runs.append_run(SLUG, _start_row("r3", pid=DEAD_PID))          # died runner

    b = board.build_board()
    assert b["totals"]["cost_usd"] == 0.30
    assert b["totals"]["live"] == 1
    assert b["slugs"][SLUG]["session_id"] == "sid-blk"
    (att,) = b["attention"]
    assert att["slug"] == SLUG
    joined = " ".join(att["reasons"])
    assert "blocked" in joined and "denial" in joined and "died" in joined
    assert att["resume"] == f"bin/fleet resume {SLUG} --directive '<how to proceed>'"
    assert "sid-blk" in att["resume_raw"]

    assert fleet_main(["board"]) == exit_codes.EXIT_OK
    text = capsys.readouterr().out
    assert "needs attention:" in text and f"bin/fleet resume {SLUG}" in text
    assert "1/4 children live" in text

    assert fleet_main(["board", "--json"]) == exit_codes.EXIT_OK
    assert json.loads(capsys.readouterr().out)["totals"]["cost_usd"] == 0.30


def test_board_text_fresh_slug_row_and_pool_draw_label(fleet_project, capsys):
    """Field-deployment polish: a spawned-but-never-updated slug (runs
    rows, no `_fleet.json`) must not render the cryptic `—/? ?`, and the
    dollar meter reads as pool draw, not billing."""
    runs.append_run(SLUG, _end_row("r1", session="sid-1", cost=0.7165))
    assert fleet_main(["board"]) == exit_codes.EXIT_OK
    text = capsys.readouterr().out
    assert f"{SLUG} · (no state yet) → —" in text
    assert "—/? ?" not in text
    assert "est. pool draw $0.7165" in text
    assert "spent" not in text
    # JSON keys stay CLI-native for tooling
    assert fleet_main(["board", "--json"]) == exit_codes.EXIT_OK
    b = json.loads(capsys.readouterr().out)
    assert b["totals"]["cost_usd"] == 0.7165


def test_board_never_crashes_on_empty_project(fleet_project, capsys):
    assert fleet_main(["board"]) == exit_codes.EXIT_OK
    assert "nothing needs attention." in capsys.readouterr().out


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


def test_resume_without_any_session_exits_48(fleet_project, capsys):
    rc = fleet_main(["resume", SLUG])
    assert rc == exit_codes.EXIT_NO_SESSION
    err = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert err["error"] == "no_session"


def test_resume_dry_run_wraps_directive_and_targets_newest_session(
        fleet_project, capsys):
    runs.append_run(SLUG, _end_row("r1", session="sid-old"))
    runs.append_run(SLUG, _end_row("r2", session="sid-new"))
    rc = fleet_main([
        "resume", SLUG, "--directive", "focus on the flaky gate", "--dry-run",
    ])
    assert rc == exit_codes.EXIT_OK
    out = capsys.readouterr().out
    assert "--resume sid-new" in out
    # the operator prose rides inside the canonical bin/wrap envelope
    assert "<operator-directive>" in out and "focus on the flaky gate" in out
    # dry-run: no new ledger rows
    assert len(runs.load_runs(SLUG)) == 2


def test_resume_explicit_session_and_launch(fleet_project, monkeypatch):
    monkeypatch.setattr(spawn.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(spawn, "_launch_runner", lambda payload: 777)
    rc = fleet_main(["resume", SLUG, "--session", "sid-x",
                     "--directive", "carry on"])
    assert rc == exit_codes.EXIT_OK
    (row,) = runs.load_runs(SLUG)
    assert row["event"] == "resumed" and row["session_id"] == "sid-x"
    assert row["pid"] == 777
