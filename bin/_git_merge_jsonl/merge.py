"""Three-way merge algorithm for `_orchestrator_log.jsonl`.

Per implplan §C.impl.9 (and plan §C.4). Inputs are three byte-strings
(ancestor / ours / theirs); output is the merged content.

Algorithm
---------
1. Parse each side line-by-line as JSON. Parse failure on any side →
   raise `MergeImpossibleError` (exit 1 with diagnostic).
2. Compute stable hash of canonicalized JSON for set membership:
   `sha256(json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(',',':')))`
   truncated to 16 bytes.
3. Compute `new = (ours ∪ theirs) - ancestor` set-wise on hash.
4. Dedupe identical rows in `ours ∩ theirs ∩ new` (cherry-pick artifacts).
5. Sort by `ts` ascending. Stable tiebreaker:
   - Same `ts` + same `(session_id, task_id, transition.from, transition.to)`
     4-tuple → prefer the row with more non-null optional fields.
   - Tied on completeness → prefer `ours` for byte-stable output.
6. Serialize each row in canonical JSON form (sorted keys, compact
   separators) so the output is byte-stable across re-runs.

Stable canonical form is critical for the commutativity test: even if
the same logical row arrives via different writers with different key
ordering, the canonical-JSON serialization makes the byte output
identical regardless of input arrival order.
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable


class MergeImpossibleError(RuntimeError):
    """Raised when one or more inputs cannot be parsed as JSONL."""


_MAX_BYTES = 100 * 1024 * 1024  # 100 MB per §C.impl.9 step 1


def _parse_lines(content: bytes, label: str) -> list[dict]:
    """Parse JSONL bytes → list of dicts. Raises MergeImpossibleError on
    any parse failure; the diagnostic message identifies which side
    (`ancestor` / `ours` / `theirs`) and the offending line."""
    if len(content) > _MAX_BYTES:
        raise MergeImpossibleError(
            f"{label}: exceeds 100 MB limit ({len(content)} bytes)"
        )
    rows: list[dict] = []
    for lineno, raw in enumerate(content.split(b"\n"), start=1):
        if not raw:
            continue
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MergeImpossibleError(
                f"{label}: parse failure at line {lineno}: {exc}"
            ) from exc
        if not isinstance(obj, dict):
            raise MergeImpossibleError(
                f"{label}: line {lineno} is not a JSON object"
            )
        rows.append(obj)
    return rows


def _canonical_json(row: dict) -> str:
    """Canonical JSON: sorted keys, no extra whitespace, ensure_ascii=False
    (so unicode reasons stay verbatim)."""
    return json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hash_row(row: dict) -> str:
    """16-byte hex truncation of SHA-256 over the canonical form."""
    return hashlib.sha256(_canonical_json(row).encode("utf-8")).hexdigest()[:32]


def _non_null_count(row: dict) -> int:
    """Count non-null fields in a row. Used as the stable-tiebreaker
    'more complete record' signal per §C.impl.9 step 5."""
    return sum(1 for v in row.values() if v is not None)


def _tuple_key(row: dict) -> tuple:
    """The 4-tuple used as the stable-tiebreaker grouping key."""
    trans = row.get("transition") or {}
    return (
        row.get("session_id"),
        row.get("task_id"),
        trans.get("from"),
        trans.get("to"),
    )


def merge(
    ancestor_bytes: bytes, ours_bytes: bytes, theirs_bytes: bytes
) -> bytes:
    """Three-way merge of JSONL inputs. Returns merged bytes (with
    trailing newline if non-empty).

    Per implplan §C.impl.9 algorithm. Raises `MergeImpossibleError` on
    parse failure on any side.
    """
    ancestor = _parse_lines(ancestor_bytes, "ancestor")
    ours = _parse_lines(ours_bytes, "ours")
    theirs = _parse_lines(theirs_bytes, "theirs")

    anc_hashes = {_hash_row(r) for r in ancestor}

    # Build a dict keyed by hash so dedupe is cheap and we can apply the
    # ours-vs-theirs stable-tiebreaker for the byte-identical case.
    union_by_hash: dict[str, dict] = {}

    # Include ancestor rows themselves (they are inherited by both
    # sides; merge must preserve them so the no-loss property holds).
    for row in ancestor:
        union_by_hash[_hash_row(row)] = row
    # Layer ours rows. If hash collision, identical row — no change.
    for row in ours:
        h = _hash_row(row)
        if h not in union_by_hash:
            union_by_hash[h] = row
        # Else identical — keep existing. (No semantic preference needed
        # because the bytes are identical at this point.)
    # Layer theirs rows.
    for row in theirs:
        h = _hash_row(row)
        if h not in union_by_hash:
            union_by_hash[h] = row

    # Now apply stable-tiebreaker for "same ts + same 4-tuple" sets.
    # Group by (ts, 4-tuple) and within each group prefer the more
    # complete record; tied → prefer ours.
    ours_hashes = {_hash_row(r) for r in ours}
    grouped: dict[tuple, list[dict]] = {}
    for row in union_by_hash.values():
        key = (row.get("ts"), _tuple_key(row))
        grouped.setdefault(key, []).append(row)

    resolved: list[dict] = []
    for key, group in grouped.items():
        if len(group) == 1:
            resolved.append(group[0])
            continue
        # Multiple rows with same (ts, 4-tuple). Apply tiebreaker.
        def _sort_key(r: dict) -> tuple:
            return (
                -_non_null_count(r),  # more non-null first (negated for ascending sort)
                0 if _hash_row(r) in ours_hashes else 1,  # ours first
            )

        group_sorted = sorted(group, key=_sort_key)
        resolved.append(group_sorted[0])

    # Final sort by ts ascending (primary key); secondary by canonical
    # form for byte-stable output across re-runs.
    def _final_sort_key(r: dict) -> tuple:
        return (r.get("ts", ""), _canonical_json(r))

    resolved.sort(key=_final_sort_key)

    if not resolved:
        return b""
    body = "\n".join(_canonical_json(r) for r in resolved) + "\n"
    return body.encode("utf-8")
