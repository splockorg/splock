"""Baseline mint flow (§J.impl.7).

`bin/eval-baseline --mint <name>`: validate name; snapshot active case
ids; capture per-case scores by replaying through current system config;
write manifest + case_scores.jsonl + notes.md.

For v2.7, the per-case "replay" is a synthetic capture — the actual
replay infrastructure is operator-driven (see §J.impl.6 promotion). The
mint flow captures the case's existing `expected_outcome_details` as
the baseline expectation; future invocations of `bin/eval-gate` compare
new scores against this snapshot.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import secrets
from typing import Optional

from bin._eval_common import regression_case
from bin._jsonl_log.writer import append_row
from bin._render_plan.atomic_write import write_atomic

from .exit_codes import (
    EXIT_ATOMIC_WRITE_FAILED,
    EXIT_DATASET_EMPTY,
    EXIT_OK,
    EXIT_USAGE,
)
from .manifest import (
    InvalidBaselineNameError,
    baseline_dir,
    build_manifest,
    validate_name,
    write_manifest,
)


def _now_iso_z() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _append_case_score_row(target: pathlib.Path, row: dict) -> None:
    payload = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    with target.open("ab") as fh:
        fh.write(payload.encode("utf-8"))
        fh.flush()
        os.fsync(fh.fileno())


def mint(
    plan_dir: pathlib.Path,
    *,
    name: str,
    repo_root: pathlib.Path,
    notes_text: Optional[str] = None,
) -> int:
    """Mint a baseline; return exit code."""
    try:
        validate_name(name)
    except InvalidBaselineNameError as exc:
        print(f"invalid baseline name: {exc}", file=__import__("sys").stderr)
        return EXIT_USAGE

    # Snapshot active case ids.
    cases = regression_case.list_cases(plan_dir, include_retired=False)
    if not cases:
        print("baseline mint: no active regression cases", file=__import__("sys").stderr)
        return EXIT_DATASET_EMPTY

    bdir = baseline_dir(plan_dir, name)
    if bdir.exists():
        print(
            f"baseline {name!r} already exists at {bdir}",
            file=__import__("sys").stderr,
        )
        return EXIT_USAGE
    bdir.mkdir(parents=True, exist_ok=False)

    case_ids = [c["case_id"] for c in cases]
    manifest = build_manifest(
        name=name,
        case_ids=case_ids,
        repo_root=repo_root,
    )

    # Write manifest.
    try:
        write_manifest(bdir / "manifest.json", manifest)
    except OSError as exc:
        print(f"atomic_write_failed: {exc}", file=__import__("sys").stderr)
        return EXIT_ATOMIC_WRITE_FAILED

    # case_scores.jsonl — one row per case capturing the baseline expectation.
    scores_target = bdir / "case_scores.jsonl"
    scores_target.touch()
    for case in cases:
        row = {
            "schema_version": 2,
            "row_type": "emission",
            # Schema requires ^score_[0-9a-f]{8}$ — use token_hex like
            # score_writer._new_score_id() does. Baseline-specific
            # provenance lives in scorer_attributes, not in the ID.
            "score_id": f"score_{secrets.token_hex(4)}",
            "ts": _now_iso_z(),
            "scorer_id": "test_runner_exit",  # Generic placeholder kind.
            "trace_id": f"baseline:{name}",
            "session_id": f"baseline:{name}",
            "task_id": case["case_id"],
            "score_value": case["expected_outcome"],
            "score_category": "pass",
            "scorer_attributes": {
                "baseline_name": name,
                "case_id": case["case_id"],
                "expected_outcome": case["expected_outcome"],
                "expected_outcome_details": case.get("expected_outcome_details", {}),
            },
        }
        _append_case_score_row(scores_target, row)

    # §J.impl.7 step 5: emit `baseline_minted` row to _orchestrator_log.jsonl
    # (per F-05 of §J mid-section review 2026-05-21).
    commit_sha = manifest.get("commit_sha", "unknown")
    try:
        append_row(
            plan_dir,
            {
                "ts": _now_iso_z(),
                "event_type": "baseline_minted",
                "reason": (
                    f"minted baseline {name} with {len(case_ids)} cases "
                    f"at commit {commit_sha}"
                ),
                "transition": {"from": "ready", "to": "done"},
                "extra": {
                    "baseline_name": name,
                    "case_count": len(case_ids),
                    "commit_sha": commit_sha,
                },
            },
            "bin/eval-baseline",
        )
    except Exception as exc:  # noqa: BLE001 — best-effort forensic emission
        print(
            f"warning: failed to emit baseline_minted row: {exc}",
            file=__import__("sys").stderr,
        )

    # notes.md.
    notes_body = notes_text or f"# Baseline {name}\n"
    if not notes_body.endswith("\n"):
        notes_body += "\n"
    try:
        write_atomic(bdir / "notes.md", notes_body)
    except OSError as exc:
        print(f"atomic_write_failed (notes.md): {exc}", file=__import__("sys").stderr)
        return EXIT_ATOMIC_WRITE_FAILED

    print(f"minted baseline {name} ({len(case_ids)} cases) at {bdir}")
    return EXIT_OK


def read_case_scores(plan_dir: pathlib.Path, name: str) -> list[dict]:
    target = baseline_dir(plan_dir, name) / "case_scores.jsonl"
    if not target.exists():
        return []
    out: list[dict] = []
    for raw in target.read_bytes().splitlines():
        if not raw:
            continue
        try:
            out.append(json.loads(raw.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return out


__all__ = ["mint", "read_case_scores"]
