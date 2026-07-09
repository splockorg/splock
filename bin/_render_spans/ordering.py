"""Deterministic span ordering helpers (per §J.impl.3 step 7)."""

from __future__ import annotations

from typing import Iterable

from .span_shape import Span


def sort_spans(spans: Iterable[Span]) -> list[Span]:
    """Sort by `start_ts` ascending; stable secondary by `span_id`.

    Stable secondary sort produces byte-identical output across runs
    (per test_render_spans determinism contract).
    """
    return sorted(spans, key=lambda s: (s.start_ts, s.span_id))


__all__ = ["sort_spans"]
