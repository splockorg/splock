"""J.15 — R4 = `yes-flagged` halts chain w/ exit 17 + emits forensic log row.

Per inventory + userguide §13.3 (code 17 = "Test retry cap exhausted
OR R4 tampering flagged") + userguide §13.4 step 3 "If R4 is
yes-flagged, inspect the flagged test-file edits".

The mechanism:
1. `iteration_loop.run_loop()` returns `IterationResult.HALT_TAMPERING`
   on R4 == "yes-flagged" via `rubric_mod.is_tampering_flagged(...)`.
2. `retry_loop.main._run_test_step()` maps HALT_TAMPERING to
   `halt_reason="tampering_detected"` + exit code 17
   (`EXIT_RETRY_EXCEEDED`).
3. `halt_handoff.write_halt_entry(halt_reason="tampering_detected", ...)`
   appends a morning-review section AND emits a structured row to
   `_orchestrator_log.jsonl` via the `_emit_deferral_row` helper.

This test exercises the discriminator: the orchestrator-log row must
let the operator distinguish "test retry cap" (sibling halt also at
code 17) from "R4 tampering" without re-reading the morning-review
markdown. Accepted discriminators (either is sufficient):
- Top-level `tamper_flagged: true` boolean field, OR
- `reason` substring `halt_reason=tampering_detected`.
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path


pytestmark = pytest.mark.acceptance


def _read_orchestrator_log_rows(plan_dir: Path) -> list[dict]:
    log = plan_dir / "_orchestrator_log.jsonl"
    if not log.exists():
        return []
    rows: list[dict] = []
    for line in log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _has_tamper_discriminator(row: dict) -> bool:
    """A row distinguishes 'R4 tampering' from generic 'retry exceeded' if it
    has either an explicit `tamper_flagged: true` field OR the reason
    substring `halt_reason=tampering_detected`.
    """
    if row.get("tamper_flagged") is True:
        return True
    reason = str(row.get("reason", ""))
    return "tampering_detected" in reason


def test_exit_code_17_is_emitted_on_halt_tampering_path(repo_root):
    """J.15a: retry-loop main maps HALT_TAMPERING → EXIT_RETRY_EXCEEDED (17)."""
    from bin._retry_loop import exit_codes
    # Direct source-of-truth check — `EXIT_RETRY_EXCEEDED` is the
    # documented (and singular) code emitted by HALT_TAMPERING.
    assert exit_codes.EXIT_RETRY_EXCEEDED == 17, (
        f"§F retry-loop EXIT_RETRY_EXCEEDED expected 17 (per A.impl.3a + "
        f"userguide §13.3); got {exit_codes.EXIT_RETRY_EXCEEDED}"
    )

    # Walk the main.py source to verify HALT_TAMPERING → EXIT_RETRY_EXCEEDED.
    main_src = (repo_root / "bin" / "_retry_loop" / "main.py").read_text(encoding="utf-8")
    # Look for the explicit mapping inside _run_test_step (line ~282-284 today).
    assert "HALT_TAMPERING" in main_src and "EXIT_RETRY_EXCEEDED" in main_src, (
        "expected HALT_TAMPERING handler to reference EXIT_RETRY_EXCEEDED in "
        "bin/_retry_loop/main.py — wiring may have moved"
    )


def test_halt_handoff_emits_orchestrator_log_row_with_tamper_discriminator(
    tmp_slug_dir,
):
    """J.15b: write_halt_entry(halt_reason='tampering_detected', ...) emits
    an _orchestrator_log.jsonl row with a tamper discriminator."""
    from bin._retry_loop import halt_handoff
    from bin._retry_loop.iteration_loop import IterationRecord

    record = IterationRecord(
        iteration_n=1,
        started_at="2026-05-22T12:00:00Z",
        ended_at="2026-05-22T12:01:00Z",
        test_runner_exit_code=1,
        failing_tests=["tests/foo/test_bar.py::test_x"],
        diff_excerpt="@@ -1 +1 @@\n-pass\n+raise\n",
        rubric={
            "R1_root_cause": "edit broadens assertion to mask failure",
            "R2_what_missed": "regression coverage",
            "R3_next_action": "revert edit + apply targeted fix",
            "R4_tampering": "yes-flagged",
            "R5_confidence": "high",
        },
    )

    halt_handoff.write_halt_entry(
        tmp_slug_dir,
        slug="acceptance_j15",
        chain_id="chain_2026-05-22T12:00:00Z_j15tamper",
        halt_reason="tampering_detected",
        iteration_records=[record],
    )

    # Morning-review file should exist with the tampering_detected section.
    mr_dir = tmp_slug_dir / "morning-review"
    assert mr_dir.is_dir(), "morning-review dir not created by halt_handoff"
    md_files = list(mr_dir.glob("*.md"))
    assert len(md_files) == 1, f"expected exactly 1 morning-review file; got {len(md_files)}"
    body = md_files[0].read_text(encoding="utf-8")
    assert "tampering_detected" in body, (
        "morning-review section missing `halt_reason: tampering_detected` line — "
        "operator can't determine the halt cause from the file"
    )
    assert "Final exit code:** 17" in body or "exit code: 17" in body.lower(), (
        "morning-review section missing 'exit code 17' — operator can't "
        f"correlate with the chain return code. body:\n{body[:500]}"
    )

    # Orchestrator-log row must be emitted with a tamper discriminator.
    rows = _read_orchestrator_log_rows(tmp_slug_dir)
    assert rows, "no _orchestrator_log.jsonl rows emitted by halt_handoff"

    tamper_rows = [r for r in rows if _has_tamper_discriminator(r)]
    assert tamper_rows, (
        "no orchestrator-log row carries a tamper discriminator. "
        "Expected either `tamper_flagged: true` field OR "
        "`reason` substring `halt_reason=tampering_detected`. "
        f"All emitted rows: {rows}"
    )

    # Discriminator must come with `transition.to == 'deferred'` so the
    # log-side state inference matches "halted, not completed".
    r = tamper_rows[0]
    transition = r.get("transition", {})
    assert transition.get("to") == "deferred", (
        f"R4 tamper row must transition to=deferred; got {transition!r}"
    )


def test_halt_reason_enum_includes_tampering_detected(repo_root):
    """J.15c: HaltReason Literal type still contains 'tampering_detected'.

    Drift guard — if a refactor renamed the enum value, both the
    operator-facing discriminator AND the morning-review next-action
    branch would silently change shape.
    """
    src = (repo_root / "bin" / "_retry_loop" / "halt_handoff.py").read_text(
        encoding="utf-8"
    )
    assert '"tampering_detected"' in src, (
        "HaltReason closed enum must include `tampering_detected` literal "
        "(per F.9.5 deferral_reason taxonomy)"
    )
