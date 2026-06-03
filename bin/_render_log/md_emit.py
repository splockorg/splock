"""Per-line MD emission for `bin/render_log`.

Per implplan §C.impl.10 step 2b and plan §1.E.2.iii. Canonical one-line
format:

    <ts> | <session_id> | <task_id> | <from> → <to> | overnight=<0|1> guardrail=<0|1> op_override=<0|1> op_override_state=<0|1> | reason: <truncated_reason> | pointer: <pointer_or_dash>

Notes:
- `op_override` / `op_override_state` are emitted as 0 when
  `override_in_effect` is null (i.e., no override attempted). They are
  emitted as 1 only when the corresponding field is explicitly true.
- `pointer: -` when the row's pointer is null per plan §1.E pointer
  convention.
- `task_id: -` when the row has no associated task (e.g., recovery rows).

Under `--llm-consumable` the renderer additionally emits the wrapped
`<external-content>` delimiter form per implplan §C.impl.7 below the
canonical MD line.
"""

from __future__ import annotations

import json
import pathlib
from typing import Iterator

from bin._jsonl_log.delimiter import wrap_reason
from bin._jsonl_log.reader import CorruptRow
from .truncation import truncate_reason


def _fmt_bool_as_01(value) -> str:
    """Render bool/null as `0`/`1`; null → `0` for the canonical MD shape."""
    if value is True:
        return "1"
    return "0"


def render_row(row: dict, lineno: int, *, llm_consumable: bool = False) -> str:
    """Render a single non-corrupt row into the canonical MD line.

    Pure function; no I/O. Returns one or two lines depending on
    `llm_consumable` mode.
    """
    ts = row.get("ts", "<missing-ts>")
    session_id = row.get("session_id", "<missing-session_id>")
    task_id = row.get("task_id") or "-"
    trans = row.get("transition") or {}
    t_from = trans.get("from", "?")
    t_to = trans.get("to", "?")
    mode = row.get("mode_at_transition") or {}
    overnight = _fmt_bool_as_01(mode.get("overnight"))
    guardrail = _fmt_bool_as_01(mode.get("guardrail"))
    override = row.get("override_in_effect") or {}
    if not isinstance(override, dict):
        override = {}
    op_override = _fmt_bool_as_01(override.get("operator_override"))
    op_override_state = _fmt_bool_as_01(override.get("operator_override_state"))
    pointer = row.get("pointer") or "-"
    reason_raw = row.get("reason", "")
    reason_rendered = truncate_reason(reason_raw, lineno)
    body = (
        f"{ts} | {session_id} | {task_id} | {t_from} → {t_to} | "
        f"overnight={overnight} guardrail={guardrail} "
        f"op_override={op_override} op_override_state={op_override_state} | "
        f"reason: {reason_rendered} | pointer: {pointer}"
    )
    if llm_consumable:
        wrap = wrap_reason(row_id=lineno, reason=reason_raw)
        return f"{body}\n{wrap}"
    return body


def render_corrupt(corrupt: CorruptRow) -> str:
    """Render a corrupt-line marker MD line."""
    snippet = corrupt.raw_bytes[:80].decode("utf-8", errors="replace")
    return (
        f"_corrupt | line={corrupt.line_number} | "
        f"raw_snippet (≤ 80 bytes): {snippet}"
    )


def iter_md_lines(
    jsonl_path: pathlib.Path, *, llm_consumable: bool = False
) -> Iterator[str]:
    """Yield MD lines for each JSONL row.

    Reads the file directly so we keep authoritative line numbers
    (1-indexed) without depending on a helper that re-enumerates.

    Empty / missing file → yields nothing (no header).
    """
    if not jsonl_path.exists() or jsonl_path.stat().st_size == 0:
        return
    with jsonl_path.open("rb") as fh:
        for lineno, raw in enumerate(fh, start=1):
            stripped = raw.rstrip(b"\n")
            if not stripped:
                continue
            try:
                obj = json.loads(stripped.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                yield render_corrupt(
                    CorruptRow(line_number=lineno, raw_bytes=stripped)
                )
                continue
            if not isinstance(obj, dict):
                yield render_corrupt(
                    CorruptRow(line_number=lineno, raw_bytes=stripped)
                )
                continue
            yield render_row(obj, lineno, llm_consumable=llm_consumable)
