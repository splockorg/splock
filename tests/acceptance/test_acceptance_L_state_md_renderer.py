"""L — end-to-end acceptance test for the `_state.json` → `_orchestrator.md`
renderer wired through `bin/update_orchestrator` (per orch_status_render T6).

Drives every cross-cutting concern of the orch_status_render Phase 3
substrate in one chain:

- 7-status glyph rendering (ready/wip/done/deferred/blocked/cancelled/unknown)
- Rolled-up phase status semantics (blocked-wins override + unknown-still-alive
  preventing premature "complete")
- Operator-notes preservation across 7 re-renders (sentinel survives)
- JSONL log sequence: `state_md_rendered` precedes `transition` for every step
- Option A boundary: the slug-prefixed `<slug>_orchestrator.md` plan-substrate
  render is byte-identical to its baseline after every state mutation
- KNOWN_WRITERS attribution: every emitted row carries
  `emitted_by="bin/update_orchestrator"`
- Subprocess invocation (mirrors chain-overnight): each transition is driven
  via `python -m bin._update_orchestrator.main` rather than in-process
  `dispatch()` calls, exercising the real CLI argparse + process boundary

The CLI's `_repo_root()` is hardcoded to the real project root via
`Path(__file__).resolve().parents[2]`. Tests use the `run_update_orchestrator.py`
wrapper (staged from the fixture dir into tmp_path) to inject a fake-repo
root so the subprocess writes into `tmp_path/fake_repo/docs/plans/<slug>/`
instead of the live tree. The wrapper itself executes as a subprocess and
delegates to the real `bin._update_orchestrator.main.main()`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest


pytestmark = pytest.mark.acceptance


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "acceptance" / "L"

SLUG = "test_acceptance_l_plan"

# Hex-only 8-char session id per orchestrator_log_v1 SessionId pattern
# (^sess_[0-9a-f]{8}$).
SESSION_ID = "sess_acce11de"

# Operator-notes sentinel content. The chain MUST preserve these across
# all 7 re-renders.
OPERATOR_NOTES_CONTENT = (
    "## Operator notes for L\n"
    "\n"
    "- Investigating retry pattern in T2 -- check `_orchestrator_log.jsonl` "
    "after step 6.\n"
    "- Sentinel: ACCEPTANCE_L_RUN_2026_05_23"
)
SENTINEL_STRING = "ACCEPTANCE_L_RUN_2026_05_23"
NOTES_HEADER = "## Operator notes for L"

# Anchor markers — must match `bin/_render_plan/human_notes.py`.
BEGIN_ANCHOR_PREFIX = "<!-- BEGIN-HUMAN-NOTES"
END_ANCHOR_PREFIX = "<!-- END-HUMAN-NOTES"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stage_fake_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Set up tmp_path/fake_repo/docs/plans/<slug>/ with seed files.

    Returns (fake_repo, plan_dir, wrapper_script_path).
    """
    fake_repo = tmp_path / "fake_repo"
    plan_dir = fake_repo / "docs" / "plans" / SLUG
    plan_dir.mkdir(parents=True)

    # Seed `_state.json` (dict-form per state_writer.py contract).
    shutil.copy2(FIXTURES_DIR / "_state.json", plan_dir / "_state.json")
    # Seed plan substrate `<slug>_orchestrator.json`.
    shutil.copy2(
        FIXTURES_DIR / f"{SLUG}_orchestrator.json",
        plan_dir / f"{SLUG}_orchestrator.json",
    )

    # Stage the wrapper script into tmp_path.
    wrapper = tmp_path / "run_update_orchestrator.py"
    shutil.copy2(FIXTURES_DIR / "run_update_orchestrator.py", wrapper)

    return fake_repo, plan_dir, wrapper


def _drive_update(
    wrapper: Path,
    fake_repo: Path,
    slug: str,
    task_id: str,
    status: str,
    *,
    pointer: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """Run `python <wrapper> <fake_repo> <slug> <task_id> <status> [--pointer P]`
    from the real repo root so module imports resolve, capturing output.
    """
    cmd = [sys.executable, str(wrapper), str(fake_repo), slug, task_id, status]
    if pointer is not None:
        cmd.extend(["--pointer", pointer])

    env = os.environ.copy()
    env["CLAUDE_SESSION_ID"] = SESSION_ID
    # Ensure the subprocess can import `bin.*` from the real repo.
    pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{REPO_ROOT}{os.pathsep}{pythonpath}" if pythonpath else str(REPO_ROOT)
    )

    return subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),  # so `import bin._update_orchestrator...` resolves
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _render_plan_substrate(fake_repo: Path, plan_dir: Path) -> str:
    """Render the slug-prefixed plan substrate via `bin/render_plan --from-json`.

    Returns the rendered MD content. Uses --from-json so the renderer
    writes adjacent to the JSON instead of resolving via _PLANS_DIR (which
    points at the real repo).
    """
    orch_json = plan_dir / f"{SLUG}_orchestrator.json"
    orch_md = plan_dir / f"{SLUG}_orchestrator.md"

    cmd = [
        sys.executable,
        "-m",
        "bin._render_plan.main",
        "--kind",
        "orchestrator",
        "--from-json",
        str(orch_json),
    ]
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{REPO_ROOT}{os.pathsep}{pythonpath}" if pythonpath else str(REPO_ROOT)
    )
    result = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"bin/render_plan --kind orchestrator failed: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert orch_md.exists(), "plan substrate MD not written"
    return orch_md.read_text(encoding="utf-8")


def _read_state_md(plan_dir: Path) -> str:
    md_path = plan_dir / "_orchestrator.md"
    assert md_path.exists(), f"_orchestrator.md not found at {md_path}"
    return md_path.read_text(encoding="utf-8")


def _read_log_rows(plan_dir: Path) -> list[dict]:
    """Read every JSONL row from `_orchestrator_log.jsonl`."""
    log = plan_dir / "_orchestrator_log.jsonl"
    if not log.exists():
        return []
    rows: list[dict] = []
    for line in log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _inject_operator_notes(md_content: str, new_notes: str) -> str:
    """Replace content between BEGIN-HUMAN-NOTES and END-HUMAN-NOTES anchors."""
    lines = md_content.split("\n")
    begin_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if line.startswith(BEGIN_ANCHOR_PREFIX):
            begin_idx = i
        elif line.startswith(END_ANCHOR_PREFIX):
            end_idx = i
            break
    assert begin_idx is not None, "BEGIN-HUMAN-NOTES anchor missing"
    assert end_idx is not None, "END-HUMAN-NOTES anchor missing"
    assert begin_idx < end_idx, "anchors in wrong order"

    # Replace everything between begin_idx+1 and end_idx (exclusive) with the
    # new notes content. Preserve the surrounding blank lines around the
    # injected block so the renderer's anchor-content extraction returns
    # the notes verbatim.
    new_lines = (
        lines[: begin_idx + 1]
        + ["", new_notes, ""]
        + lines[end_idx:]
    )
    return "\n".join(new_lines)


def _assert_glyph_present(md_content: str, glyph: str, task_id: str) -> None:
    """Assert at least one MD line contains both the glyph and the task_id."""
    for line in md_content.splitlines():
        if glyph in line and task_id in line:
            return
    pytest.fail(
        f"expected glyph {glyph!r} on task row for {task_id}; not found in MD"
    )


def _assert_rolled_up(md_content: str, expected: str) -> None:
    """Assert the rendered MD contains the expected rolled-up phase status."""
    # The template puts `{rolled_up_phase_status}` directly under `## Phase
    # status` heading. Find the heading and check the next non-blank line.
    lines = md_content.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "## Phase status":
            # Search forward for first non-blank line.
            for next_line in lines[i + 1 :]:
                if next_line.strip():
                    assert next_line.strip() == expected, (
                        f"rolled-up phase status: expected {expected!r}, "
                        f"got {next_line.strip()!r}"
                    )
                    return
    pytest.fail("`## Phase status` heading not found in rendered MD")


def _assert_notes_preserved(md_content: str) -> None:
    assert SENTINEL_STRING in md_content, (
        f"sentinel string {SENTINEL_STRING!r} missing from MD — "
        f"operator-notes preservation broken"
    )
    assert NOTES_HEADER in md_content, (
        f"notes header {NOTES_HEADER!r} missing from MD — "
        f"operator-notes preservation broken"
    )


# ---------------------------------------------------------------------------
# The one acceptance test — exercises every concern in a single chain
# ---------------------------------------------------------------------------


def test_acceptance_L_drives_seven_status_chain(tmp_path: Path) -> None:
    """End-to-end: 7 transitions, MD re-render after each, notes survive,
    log JSONL emits state_md_rendered before each transition, plan-substrate
    MD byte-identical to baseline after every mutation.
    """
    # ---- Setup -----------------------------------------------------------
    fake_repo, plan_dir, wrapper = _stage_fake_repo(tmp_path)

    # Render the plan substrate ONCE to capture the baseline. The
    # subsequent `bin/update_orchestrator` invocations MUST NOT touch this
    # file (Option A boundary).
    plan_substrate_baseline = _render_plan_substrate(fake_repo, plan_dir)
    plan_substrate_path = plan_dir / f"{SLUG}_orchestrator.md"
    plan_substrate_baseline_bytes = plan_substrate_path.read_bytes()

    # ---- Step 1: T1 ready -> wip ----------------------------------------
    # Drive the first transition. This produces the initial `_orchestrator.md`
    # via the wired renderer (bootstrap notes placeholder lives between
    # anchors at this point).
    result = _drive_update(wrapper, fake_repo, SLUG, "T1", "wip")
    assert result.returncode == 0, (
        f"step 1 failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    md_after_step1 = _read_state_md(plan_dir)
    _assert_glyph_present(md_after_step1, "✈️", "T1")
    # All other tasks still ready; rolled-up = "in progress".
    _assert_rolled_up(md_after_step1, "in progress")
    # 5 ready legend pairs still appear (4 task rows remain ready).
    assert md_after_step1.count("🕛") >= 4, (
        f"expected at least 4 ready glyphs (legend + 4 task rows); "
        f"got {md_after_step1.count('🕛')}"
    )

    # ---- Inject operator notes between anchors --------------------------
    edited_md = _inject_operator_notes(md_after_step1, OPERATOR_NOTES_CONTENT)
    (plan_dir / "_orchestrator.md").write_text(edited_md, encoding="utf-8")
    # Sanity: edits include the sentinel.
    assert SENTINEL_STRING in (plan_dir / "_orchestrator.md").read_text(
        encoding="utf-8"
    )
    # Plan substrate unaffected.
    assert plan_substrate_path.read_bytes() == plan_substrate_baseline_bytes

    # ---- Step 2: T1 wip -> done -----------------------------------------
    result = _drive_update(wrapper, fake_repo, SLUG, "T1", "done")
    assert result.returncode == 0, (
        f"step 2 failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    md = _read_state_md(plan_dir)
    _assert_glyph_present(md, "✅", "T1")
    # T2-T5 still ready, T1 done → mix of done + ready, no terminals only,
    # no wip/unknown/blocked → "in progress" per the rolled-up rule.
    _assert_rolled_up(md, "in progress")
    _assert_notes_preserved(md)
    assert plan_substrate_path.read_bytes() == plan_substrate_baseline_bytes

    # ---- Step 3: T2 ready -> blocked ------------------------------------
    result = _drive_update(wrapper, fake_repo, SLUG, "T2", "blocked")
    assert result.returncode == 0, (
        f"step 3 failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    md = _read_state_md(plan_dir)
    _assert_glyph_present(md, "❌", "T2")
    # blocked-wins override → rolled-up = "blocked"
    _assert_rolled_up(md, "blocked")
    _assert_notes_preserved(md)
    assert plan_substrate_path.read_bytes() == plan_substrate_baseline_bytes

    # ---- Step 4: T3 ready -> deferred (with --pointer) ------------------
    result = _drive_update(
        wrapper,
        fake_repo,
        SLUG,
        "T3",
        "deferred",
        pointer="scheduled_markers/list.md#L.deferred",
    )
    assert result.returncode == 0, (
        f"step 4 failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    md = _read_state_md(plan_dir)
    _assert_glyph_present(md, "📅", "T3")
    # T2 still blocked → blocked-wins
    _assert_rolled_up(md, "blocked")
    _assert_notes_preserved(md)
    assert plan_substrate_path.read_bytes() == plan_substrate_baseline_bytes

    # ---- Step 5: T4 ready -> cancelled ----------------------------------
    result = _drive_update(wrapper, fake_repo, SLUG, "T4", "cancelled")
    assert result.returncode == 0, (
        f"step 5 failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    md = _read_state_md(plan_dir)
    _assert_glyph_present(md, "🚫", "T4")
    _assert_rolled_up(md, "blocked")  # T2 blocked still wins
    _assert_notes_preserved(md)
    assert plan_substrate_path.read_bytes() == plan_substrate_baseline_bytes

    # ---- Step 6: T5 ready -> unknown ------------------------------------
    result = _drive_update(wrapper, fake_repo, SLUG, "T5", "unknown")
    assert result.returncode == 0, (
        f"step 6 failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    md = _read_state_md(plan_dir)
    _assert_glyph_present(md, "❓", "T5")
    _assert_rolled_up(md, "blocked")  # T2 blocked still wins
    _assert_notes_preserved(md)
    assert plan_substrate_path.read_bytes() == plan_substrate_baseline_bytes

    # ---- Step 7: T2 blocked -> done (resolves blocker) ------------------
    # After this: {done (T1+T2), deferred (T3), cancelled (T4), unknown (T5)}
    # Per _compute_rolled_up_phase_status:
    #   - blocked NOT present
    #   - unknown IS present → "in progress" (per spec correction)
    result = _drive_update(wrapper, fake_repo, SLUG, "T2", "done")
    assert result.returncode == 0, (
        f"step 7 failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    md = _read_state_md(plan_dir)
    _assert_glyph_present(md, "✅", "T2")
    # T5 unknown is still alive → "in progress" (NOT "complete")
    _assert_rolled_up(md, "in progress")
    _assert_notes_preserved(md)
    assert plan_substrate_path.read_bytes() == plan_substrate_baseline_bytes

    # ---- Log JSONL audit ------------------------------------------------
    rows = _read_log_rows(plan_dir)
    transition_rows = []
    state_md_rendered_rows = []
    for row in rows:
        # `state_md_rendered` rows are observability rows with event_type set
        # and transition.from == transition.to == "unknown".
        if row.get("event_type") == "state_md_rendered":
            state_md_rendered_rows.append(row)
            continue
        trans = row.get("transition", {})
        if (
            trans.get("from") != "unknown" or trans.get("to") != "unknown"
        ) and "event_type" not in row:
            # Canonical transition row (not an observability row, not a refusal
            # row carrying an event_type discriminator).
            transition_rows.append(row)

    assert len(transition_rows) == 7, (
        f"expected exactly 7 canonical transition rows; got "
        f"{len(transition_rows)}: "
        f"{[(r.get('task_id'), r.get('transition')) for r in transition_rows]}"
    )
    assert len(state_md_rendered_rows) == 7, (
        f"expected exactly 7 state_md_rendered rows; got "
        f"{len(state_md_rendered_rows)}"
    )

    # Every row attributed to bin/update_orchestrator.
    for row in transition_rows + state_md_rendered_rows:
        assert row.get("emitted_by") == "bin/update_orchestrator", (
            f"row carries unexpected emitter: {row.get('emitted_by')!r} "
            f"(expected 'bin/update_orchestrator')"
        )

    # state_md_rendered precedes the matching transition row in JSONL
    # ordering. Walk the full row stream and confirm each state_md_rendered
    # is immediately followed by the corresponding transition row.
    indexed = [
        (idx, row)
        for idx, row in enumerate(rows)
        if (
            row.get("event_type") == "state_md_rendered"
            or (
                row.get("transition", {}).get("from") != "unknown"
                or row.get("transition", {}).get("to") != "unknown"
            )
            and "event_type" not in row
        )
    ]
    # Pair them up: 7 pairs of (state_md_rendered, transition).
    assert len(indexed) == 14, (
        f"expected 14 ordered observability+transition rows; got {len(indexed)}"
    )
    for i in range(0, 14, 2):
        idx_rendered, row_rendered = indexed[i]
        idx_trans, row_trans = indexed[i + 1]
        assert row_rendered.get("event_type") == "state_md_rendered", (
            f"pair {i // 2}: first row is not state_md_rendered "
            f"(got event_type={row_rendered.get('event_type')!r})"
        )
        assert "event_type" not in row_trans, (
            f"pair {i // 2}: second row should be canonical transition "
            f"(no event_type) but carries event_type={row_trans.get('event_type')!r}"
        )
        assert idx_rendered < idx_trans, (
            f"pair {i // 2}: state_md_rendered index {idx_rendered} "
            f"must precede transition index {idx_trans}"
        )

    # ---- Chronological order of transitions matches the chain -----------
    expected_chain = [
        ("T1", "ready", "wip"),
        ("T1", "wip", "done"),
        ("T2", "ready", "blocked"),
        ("T3", "ready", "deferred"),
        ("T4", "ready", "cancelled"),
        ("T5", "ready", "unknown"),
        ("T2", "blocked", "done"),
    ]
    actual_chain = [
        (r["task_id"], r["transition"]["from"], r["transition"]["to"])
        for r in transition_rows
    ]
    assert actual_chain == expected_chain, (
        f"transition row order drift:\nexpected: {expected_chain}\n"
        f"actual:   {actual_chain}"
    )

    # ---- Final witness: sentinel + notes header still present -----------
    final_md = _read_state_md(plan_dir)
    _assert_notes_preserved(final_md)
    # Plan substrate untouched through the whole chain.
    assert plan_substrate_path.read_bytes() == plan_substrate_baseline_bytes, (
        "slug-prefixed plan substrate MD drifted from baseline — "
        "Option A boundary violated"
    )
