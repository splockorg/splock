"""The prompt-file mechanic — "options delivered as a prompt".

A decision sheet is the full briefing prefixed with one line of reply
instructions, written to a `.txt` the operator can paste/answer later.
Deterministic pieces live here so neither the driver nor the CLI
improvises:

- :func:`count_decision_items` — how many item blocks carry an
  `**Options:**` header (drives the ≥3 in-Claude auto-offer and the
  zero-decision "nothing to sheet" rule);
- :func:`next_prompt_path` — the slug-bound numbering contract:
  `_eli5_prompt_<N>.txt`, `N = 1 + max(existing N)`, unpadded decimal,
  glob immediately before writing, never overwrite an existing N;
- :func:`build_prompt_sheet` — the paste-able body.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

PROMPT_STEM: Final[str] = "_eli5_prompt_"
_PROMPT_RE = re.compile(r"^_eli5_prompt_([1-9][0-9]*)\.txt$")

SHEET_HEADER: Final[str] = (
    "Reply with option codes, e.g. `1a-C · 2-B`. One code per decision; "
    "add free text after any code to qualify it.\n\n"
)

_OPTIONS_LINE = re.compile(r"^\*\*Options:\*\*", re.MULTILINE)


def count_decision_items(briefing_md: str) -> int:
    """Item blocks that carry an `**Options:**` header (rule 5 guarantees
    informative items have none, so this count IS the decision count)."""
    return len(_OPTIONS_LINE.findall(briefing_md))


def next_prompt_path(target_dir: Path) -> Path:
    """`<target_dir>/_eli5_prompt_<N>.txt` with N = 1 + max(existing N).

    Glob at call time (immediately before writing); the first file is
    `_eli5_prompt_1.txt`. Existing files are never overwritten — a
    numbering race at worst skips a slot, never clobbers.
    """
    top = 0
    for p in target_dir.glob(f"{PROMPT_STEM}*.txt"):
        m = _PROMPT_RE.match(p.name)
        if m:
            top = max(top, int(m.group(1)))
    return target_dir / f"{PROMPT_STEM}{top + 1}.txt"


def build_prompt_sheet(briefing_md: str) -> str:
    """The paste-able `.txt` body: one instruction line + full briefing."""
    return SHEET_HEADER + briefing_md


__all__ = [
    "PROMPT_STEM",
    "SHEET_HEADER",
    "build_prompt_sheet",
    "count_decision_items",
    "next_prompt_path",
]
