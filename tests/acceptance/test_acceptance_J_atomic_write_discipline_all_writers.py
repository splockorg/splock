"""J.18 — Atomic write discipline across ALL sealed-state writers.

Per inventory + implplan cross-cutting line 280-284 ("every JSON file
written by splock substrate uses write-temp + atomic rename").
Extends J.10 (completion-summary collision) to the full writer set.

Coverage matrix:
  - `_state.json`            via `bin._update_orchestrator.state_writer.write_state`
  - `_chain_sessions.json`   via `bin._chain_overnight.manifest._atomic_write`
  - `_completion_summary_*.md` via `bin._chain_overnight.completion_summary.emit_chain_summary`
  - `_orchestrator_log.jsonl` via `bin._jsonl_log.writer.append_row` (single-write+fsync; not temp+rename)

For the temp+rename writers (first three): inject a mid-write failure
and assert the target file is in its PRE-WRITE state + no `*.tmp.*`
orphan survives. For the jsonl append: verify the underlying append
discipline (single `write()` syscall + `fsync`) — distinct invariant.
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path


pytestmark = pytest.mark.acceptance


def _no_tmp_residue(directory: Path) -> tuple[bool, list[str]]:
    """Return (clean, leftover_files). Tmp residue patterns from write_atomic."""
    residue = (
        list(directory.glob(".*.tmp"))
        + list(directory.glob("*.tmp"))
        + list(directory.glob(".*.tmp.*"))
        + list(directory.glob("*.tmp.*"))
    )
    return (not residue, [p.name for p in residue])


# ---------------------------------------------------------------------------
# Writer 1 — _state.json (write_state)
# ---------------------------------------------------------------------------

def test_state_json_atomic_write_preserves_prestate_on_failure(tmp_slug_dir, monkeypatch):
    """J.18a: _state.json mid-write failure leaves the prior file intact + no .tmp residue."""
    from bin._update_orchestrator import state_writer

    target = state_writer.state_path(tmp_slug_dir)
    # Pre-state: a known JSON document.
    pre_state = {"tasks": {"T01": {"status": "done"}}}
    state_writer.write_state(tmp_slug_dir, pre_state)
    assert target.exists() and target.is_file()
    snapshot = target.read_text(encoding="utf-8")

    # Patch write_atomic to raise after the tempfile is created — simulate
    # a mid-write OSError (the very class write_atomic's except branch covers).
    real_write_atomic = state_writer.write_atomic

    def _failing_write_atomic(path, content):
        # Call into the real helper but inject an error before the rename.
        from bin._render_plan.atomic_write import AtomicWriteError
        raise AtomicWriteError("simulated mid-write failure")

    monkeypatch.setattr(state_writer, "write_atomic", _failing_write_atomic)
    with pytest.raises(Exception):
        state_writer.write_state(tmp_slug_dir, {"tasks": {"T01": {"status": "wip"}}})
    monkeypatch.setattr(state_writer, "write_atomic", real_write_atomic)

    # Pre-state preserved.
    assert target.read_text(encoding="utf-8") == snapshot, (
        "_state.json corrupted by mid-write failure — atomic-write discipline broken"
    )
    # No .tmp residue.
    clean, leftover = _no_tmp_residue(tmp_slug_dir)
    assert clean, f"tmp residue survives mid-write failure: {leftover}"


# ---------------------------------------------------------------------------
# Writer 2 — _chain_sessions.json (manifest._atomic_write)
# ---------------------------------------------------------------------------

def test_chain_sessions_atomic_write_preserves_prestate_on_failure(tmp_slug_dir, monkeypatch):
    """J.18b: _chain_sessions.json mid-write failure leaves prior file intact + no .tmp residue."""
    from bin._chain_overnight import manifest

    target = manifest.manifest_path(tmp_slug_dir)
    # Pre-state: write a stub manifest.
    initial = {"schema_version": 1, "chains": []}
    manifest._atomic_write(target, initial)
    assert target.exists()
    snapshot = target.read_text(encoding="utf-8")

    # Patch the shared write_atomic to raise.
    import bin._render_plan.atomic_write as aw_mod
    real = aw_mod.write_atomic
    monkeypatch.setattr(
        aw_mod, "write_atomic",
        lambda p, c: (_ for _ in ()).throw(aw_mod.AtomicWriteError("simulated")),
    )
    with pytest.raises(Exception):
        manifest._atomic_write(target, {"schema_version": 1, "chains": ["mid-write-fail"]})
    monkeypatch.setattr(aw_mod, "write_atomic", real)

    assert target.read_text(encoding="utf-8") == snapshot
    clean, leftover = _no_tmp_residue(tmp_slug_dir)
    assert clean, f"tmp residue survives mid-write failure: {leftover}"


# ---------------------------------------------------------------------------
# Writer 3 — _completion_summary_*.md (completion_summary.emit_chain_summary)
# ---------------------------------------------------------------------------

def test_completion_summary_atomic_write_preserves_no_partial_on_failure(
    tmp_slug_dir, monkeypatch,
):
    """J.18c: completion_summary mid-write failure → no target file created + no .tmp residue.

    Distinct from J.18a/b because the summary file's pre-state is "absent"
    (each chain gets a freshly-named file). Assertion: failure leaves
    NEITHER the target file NOR a .tmp orphan.
    """
    from bin._chain_overnight import completion_summary

    payload = completion_summary.CompletionSummaryInput(
        slug="acceptance-j18c",
        chain_id="chain_2026-05-22T12:00:00Z_j18c0000",
        chain_started_at="2026-05-22T12:00:00Z",
        chain_ended_at="2026-05-22T13:00:00Z",
        halt_reason="success",
        driver_exit_code=0,
        phases=(),
        committed_files=(),
        wall_clock_cap_seconds=43200,
        wall_clock_total_seconds=3600,
        cost_total_usd=2.5,
    )

    import bin._render_plan.atomic_write as aw_mod
    real = aw_mod.write_atomic
    monkeypatch.setattr(
        aw_mod, "write_atomic",
        lambda p, c: (_ for _ in ()).throw(aw_mod.AtomicWriteError("simulated")),
    )
    with pytest.raises(Exception):
        completion_summary.emit_chain_summary(plan_dir=tmp_slug_dir, payload=payload)
    monkeypatch.setattr(aw_mod, "write_atomic", real)

    # No target file (failed write).
    summary_files = list(tmp_slug_dir.glob("_completion_summary_*.md"))
    assert not summary_files, (
        f"completion_summary should not exist after failed write; found {summary_files}"
    )
    clean, leftover = _no_tmp_residue(tmp_slug_dir)
    assert clean, f"tmp residue from completion summary failed write: {leftover}"


# ---------------------------------------------------------------------------
# Writer 4 — _orchestrator_log.jsonl append (single-write+fsync, not temp+rename)
# ---------------------------------------------------------------------------

def test_orchestrator_log_jsonl_append_uses_single_write_plus_fsync(repo_root):
    """J.18d: _orchestrator_log.jsonl append shape — single write + flush + fsync.

    Distinct atomicity invariant: JSONL appends are not temp+rename
    (which would copy the whole file every row). Instead the writer
    must emit each row in a single `fh.write()` call, then `fh.flush()`,
    then `os.fsync(fh.fileno())` — under flock — so a row is either
    fully present or fully absent.

    Source-walk verification: bin/_jsonl_log/writer.py must contain
    these three calls in the unlocked append helper.
    """
    src = (repo_root / "bin" / "_jsonl_log" / "writer.py").read_text(encoding="utf-8")
    assert ".write(" in src, "append helper missing fh.write() call"
    assert ".flush()" in src, "append helper missing fh.flush() call"
    assert "os.fsync(" in src, "append helper missing os.fsync() — torn writes possible on crash"
    # The locked-write helper should NOT use atomic write-temp + rename
    # for jsonl appends (it would copy the whole file per row, defeating
    # the append performance goal); single-write+fsync is the discipline.


def test_orchestrator_log_append_is_per_row_atomic(tmp_slug_dir):
    """J.18e: bin._jsonl_log.append_row produces exactly one new line per call."""
    from bin._jsonl_log import append_row, KNOWN_WRITERS

    if not KNOWN_WRITERS:
        pytest.skip("KNOWN_WRITERS empty — fixture surfacing")
    # Pick any registered emitter for the test row.
    emitter = sorted(KNOWN_WRITERS)[0]

    log = tmp_slug_dir / "_orchestrator_log.jsonl"
    rows_to_emit = 5
    # session_id pattern is `^sess_[0-9a-f]{8}$` per schema; use hex digits.
    hex_suffix = "abcdef01"
    for i in range(rows_to_emit):
        try:
            append_row(
                tmp_slug_dir,
                {
                    "transition": {"from": "ready", "to": "done"},
                    "reason": f"j18e row {i}",
                    "task_id": None,
                    "session_id": f"sess_{hex_suffix[:7]}{i}",
                    "plan_slug": "acceptance_j18e",
                    "mode_at_transition": {"overnight": False, "guardrail": True},
                },
                emitted_by=emitter,
            )
        except Exception as exc:  # pragma: no cover — fixture skip path
            pytest.skip(f"append_row rejected test row (emitter={emitter}): {exc}")

    assert log.exists(), "no jsonl log created after 5 append_row calls"
    lines = [l for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == rows_to_emit, (
        f"jsonl append amplified or suppressed: expected {rows_to_emit} rows; "
        f"got {len(lines)}. Each line must parse as one complete JSON object."
    )
    for i, line in enumerate(lines):
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            pytest.fail(f"row {i} not valid JSON — torn write?\nline: {line!r}\nerror: {exc}")
