"""Span-writer stub — reserved for native span emission (post-v2.7).

Per splock implplan §J.impl.3: v2.7 ships derived spans via
`bin/render_spans`. Native per-emitter span writes ship later via marker
NSE (Native Span Emission). This module is a no-op stub so call sites
can already reference `span_writer.emit_span(...)` and quietly do nothing
until NSE triggers.

OPERATOR-FOLLOWUP: minting marker NSE.1 requires operator authorization
per orchestrator §9 #6. This module remains a no-op until that mint.

Mint command preview (operator runs):

    bin/marker register-prefix NSE --domain "Span emission infrastructure" \\
        --owner "§J.impl"
    bin/marker create NSE.1 "Activate native span emission" \\
        --trigger "condition:exists:_spans.jsonl AND needs_us_granularity_observed"
"""

from __future__ import annotations

import pathlib
from typing import Any


def emit_span(plan_dir: pathlib.Path, span: dict[str, Any]) -> None:
    """No-op stub. Reserved for native span emission post-NSE marker."""
    # OPERATOR-FOLLOWUP — see module docstring. Intentionally a no-op so
    # call sites can already reference this symbol without runtime effect.
    _ = plan_dir
    _ = span
    return None


__all__ = ["emit_span"]
