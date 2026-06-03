"""Existing-MD reader — file-level I/O only.

Anchor-block extraction lives in `human_notes.py`; this module is the
thin file-IO seam so `human_notes.py` can stay text-pure (easier to test
without filesystem fixtures).
"""

from __future__ import annotations

from pathlib import Path


def read_existing_md(path: Path) -> str | None:
    """Read MD file if present; return `None` if missing.

    First-render bootstrap (no prior MD) returns None so `human_notes`
    can emit the empty-anchor template per implplan §B.impl.4 lines
    1167-1169 (first-render row in the bootstrap table).
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None
