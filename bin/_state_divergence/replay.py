"""Log → derived `_state.json` replay + diff vs on-disk state.

Per implplan §C.impl.4 step 2 and plan §1.E transition rules.

The replay function folds each row's `transition.to` into the
per-`task_id` current state. Recovery rows (session_id="_recovery") are
skipped — they are forensic markers, not state transitions.

The diff function compares the derived state against the on-disk
`_state.json` and returns a structured list of divergences.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Any

from bin._jsonl_log.reader import CorruptRow, iter_rows


@dataclasses.dataclass(frozen=True)
class Divergence:
    """A single per-task-id divergence between log replay and `_state.json`."""

    task_id: str
    log_says: str | None
    state_says: str | None
    last_log_row_ref: str
    last_log_row_ts: str | None


class LogCorruptError(RuntimeError):
    """Raised when the JSONL contains an unrecoverable corrupt row.

    Per implplan §C.impl.4 exit code 2: divergence check exits 2 on
    log_corrupt; replay aborts and manual intervention is required.
    """


def replay(jsonl_path: pathlib.Path) -> tuple[dict[str, str], int, dict[str, str]]:
    """Replay the log to a per-task-id current state.

    Returns
    -------
    (derived_state, rows_consumed, last_row_ref_by_task)
        - derived_state: {task_id: current_status} mapping.
        - rows_consumed: total non-recovery rows folded.
        - last_row_ref_by_task: {task_id: "_orchestrator_log.jsonl:line=N"}
          for the last row that mutated each task. Used to populate the
          per-divergence `last_log_row_ref`.

    Raises
    ------
    LogCorruptError
        If a `CorruptRow` is encountered. The divergence check treats
        log corruption as a hard halt; render_log is the lenient reader.
    """
    derived: dict[str, str] = {}
    last_ref: dict[str, str] = {}
    last_ts: dict[str, str] = {}
    rows = 0
    for lineno, row in enumerate(iter_rows(jsonl_path), start=1):
        if isinstance(row, CorruptRow):
            raise LogCorruptError(
                f"corrupt row at line {row.line_number} in {jsonl_path}"
            )
        # Skip recovery audit rows.
        if row.get("session_id") == "_recovery":
            continue
        task_id = row.get("task_id")
        if task_id is None:
            continue  # non-task event; doesn't mutate per-task state
        trans = row.get("transition") or {}
        if not isinstance(trans, dict):
            # The shipped emitter writes `{"from": ..., "to": ...}`
            # (state_machine.emit_transition). A scalar here means the row was
            # not written by it. This tool exists to AUDIT logs, so a
            # structurally wrong row must be reported as corruption, not crash
            # the replay with an AttributeError on `.get`.
            raise LogCorruptError(
                f"row at line {lineno} in {jsonl_path} has a non-object "
                f"`transition` ({type(trans).__name__}); expected "
                f'{{"from": ..., "to": ...}}'
            )
        new_state = trans.get("to")
        if new_state is None:
            continue
        derived[task_id] = new_state
        last_ref[task_id] = f"_orchestrator_log.jsonl:line={lineno}"
        last_ts[task_id] = row.get("ts", "")
        rows += 1
    # Bundle last_ref + last_ts into one mapping for callers.
    combined = {tid: {"ref": last_ref[tid], "ts": last_ts.get(tid, "")} for tid in last_ref}
    # Return the simple-shape derived state but stash combined onto a
    # function attribute for the diff caller. Cleaner: just return a tuple
    # where the third element carries both pieces in a nested dict.
    return derived, rows, combined  # type: ignore[return-value]


def _load_state_json(state_path: pathlib.Path) -> dict[str, str]:
    """Extract the {task_id: status} mapping from `_state.json`.

    Convention (per §A + §E `_state.json` shape — kept minimal here since
    the §A.impl agent owns the canonical schema):

        {
          "tasks": {
            "T1": {"status": "done", ...},
            "T2": {"status": "wip", ...}
          },
          ...
        }

    If `_state.json` doesn't exist or doesn't have a `tasks` map, we
    return an empty dict; the diff will then show every replayed task as
    a divergence (which is correct — log says X, state says nothing).
    """
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    tasks_section = data.get("tasks")
    if not isinstance(tasks_section, dict):
        return {}
    out: dict[str, str] = {}
    for tid, payload in tasks_section.items():
        if isinstance(payload, dict) and "status" in payload:
            out[tid] = str(payload["status"])
        elif isinstance(payload, str):
            # Some plan formats store status as the value directly.
            out[tid] = payload
    return out


def diff(
    derived: dict[str, str],
    on_disk: dict[str, str],
    last_ref_combined: dict[str, dict[str, str]],
) -> list[Divergence]:
    """Compare derived state vs on-disk state. Returns a sorted list of
    divergences."""
    out: list[Divergence] = []
    all_task_ids = sorted(set(derived) | set(on_disk))
    for tid in all_task_ids:
        log_says = derived.get(tid)
        state_says = on_disk.get(tid)
        if log_says == state_says:
            continue
        ref_info = last_ref_combined.get(tid, {})
        out.append(
            Divergence(
                task_id=tid,
                log_says=log_says,
                state_says=state_says,
                last_log_row_ref=ref_info.get("ref", "_orchestrator_log.jsonl:line=?"),
                last_log_row_ts=ref_info.get("ts") or None,
            )
        )
    return out


def check_one(
    slug: str, slug_dir: pathlib.Path
) -> dict[str, Any]:
    """Run the full check for one plan dir. Returns the structured report
    dict per implplan §C.impl.4.

    Result key: "clean" | "diverged" | "log_corrupt".
    """
    jsonl = slug_dir / "_orchestrator_log.jsonl"
    state = slug_dir / "_state.json"
    import datetime

    checked_at = (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "slug": slug,
        "checked_at": checked_at,
        "log_path": str(jsonl),
        "state_path": str(state),
        "log_replay_rows": 0,
        "divergences": [],
        "result": "clean",
    }
    try:
        derived, rows, last_ref = replay(jsonl)
    except LogCorruptError:
        report["result"] = "log_corrupt"
        return report
    on_disk = _load_state_json(state)
    divs = diff(derived, on_disk, last_ref)
    report["log_replay_rows"] = rows
    report["divergences"] = [dataclasses.asdict(d) for d in divs]
    report["result"] = "diverged" if divs else "clean"
    return report
