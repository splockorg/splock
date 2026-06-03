"""--recent <N> surfacer for free-text scorers (R1/R2/R3) (§J.impl.10)."""

from __future__ import annotations

import pathlib

from bin._eval_common import score_writer


def recent_for_scorer(
    plan_dir: pathlib.Path,
    scorer_id: str,
    n: int,
) -> list[dict]:
    """Return up to N most-recent emission rows for the given scorer."""
    emissions = [
        r
        for r in score_writer.iter_rows(plan_dir)
        if r.get("row_type") == "emission" and r.get("scorer_id") == scorer_id
    ]
    emissions.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return emissions[: max(0, int(n))]


__all__ = ["recent_for_scorer"]
