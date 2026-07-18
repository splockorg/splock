"""eli5 subject resolution + the 8KB subject-truncation rule.

Two distinct concerns, both deterministic:

**The five-member subject enum.** Slug-bound `/eli5` resolves a subject
artifact per a FIXED stage precedence (NOT global mtime): the qa
artifact if any exists, else plan, else recon. research/qna are
reachable only via an explicit `--subject` — intentional: they are
inputs, not verdicts. This is eli5's OWN closed enum — qa's subject
enum (`bin/_qa/subject.py`) has four members and no `qa`; do not import
it. Within one stage, newest mtime wins among that stage's base +
numbered re-run files (`<slug>_qa.md` vs `<slug>_qa_<N>.md`; qa's
per-subject variants like `<slug>_qa_plan.md` are NOT candidates —
numeric suffixes only).

**Subject truncation.** The wrap envelope's 8KB byte cap
(`bin/_wrap/main.py`) is real and full QA reports exceed it. The rule:
truncate TAIL-FIRST at a paragraph boundary, append a visible marker
`[subject truncated at 8KB — N chars omitted]`, never refuse solely for
length, never silently truncate. `commands/eli5.md` instructs the
driver in this exact rule; the CLI applies :func:`truncate_subject`
so both surfaces behave identically.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

SUBJECTS: Final[tuple[str, ...]] = ("recon", "qna", "research", "plan", "qa")
"""eli5's closed subject enum (five members; includes `qa`)."""

DEFAULT_PRECEDENCE: Final[tuple[str, ...]] = ("qa", "plan", "recon")
"""Slug-bound default subject order: verdicts before inputs."""

SUBJECT_BYTE_CAP: Final[int] = 8192
"""Mirror of the wrap envelope's cap (`bin/_wrap/main.py` SC10)."""


def artifact_name(slug: str, subject: str) -> str:
    if subject not in SUBJECTS:
        raise ValueError(f"subject must be one of {SUBJECTS} (got {subject!r})")
    return f"{slug}_{subject}.md"


def _stage_candidates(slug_dir: Path, slug: str, stage: str) -> list[Path]:
    """`<slug>_<stage>.md` plus numeric re-run variants, existing only."""
    pattern = re.compile(rf"^{re.escape(slug)}_{re.escape(stage)}(_\d+)?\.md$")
    return [p for p in slug_dir.glob(f"{slug}_{stage}*.md")
            if pattern.match(p.name) and p.is_file()]


def resolve_slug_subject(
    slug_dir: Path, slug: str, subject: str | None = None,
) -> Path | None:
    """The slug-bound subject artifact, or None when nothing resolves.

    Explicit `subject` pins the stage (newest mtime among its own base +
    numbered files). Otherwise walk DEFAULT_PRECEDENCE and take the
    first stage with any candidate.
    """
    stages = (subject,) if subject else DEFAULT_PRECEDENCE
    for stage in stages:
        if stage not in SUBJECTS:
            raise ValueError(f"subject must be one of {SUBJECTS} (got {stage!r})")
        candidates = _stage_candidates(slug_dir, slug, stage)
        if candidates:
            return max(candidates, key=lambda p: p.stat().st_mtime)
    return None


def truncate_subject(text: str, cap: int = SUBJECT_BYTE_CAP) -> tuple[str, int]:
    """Fit `text` under the wrap byte cap. Returns (text, chars_omitted).

    Tail-first at a paragraph boundary (the last blank line that fits);
    falls back to a hard character cut when no paragraph break exists in
    range. The visible marker is part of the returned text and counted
    inside the cap. chars_omitted == 0 means untouched.
    """
    if len(text.encode("utf-8")) <= cap:
        return text, 0

    # Reserve room for the marker (worst-case digit width included).
    marker_probe = f"\n\n[subject truncated at 8KB — {len(text)} chars omitted]"
    budget = cap - len(marker_probe.encode("utf-8"))

    encoded = text.encode("utf-8")
    prefix = encoded[:budget].decode("utf-8", "ignore")
    cut = prefix.rfind("\n\n")
    if cut > 0:
        prefix = prefix[:cut]
    omitted = len(text) - len(prefix)
    marker = f"\n\n[subject truncated at 8KB — {omitted} chars omitted]"
    return prefix + marker, omitted


__all__ = [
    "DEFAULT_PRECEDENCE",
    "SUBJECTS",
    "SUBJECT_BYTE_CAP",
    "artifact_name",
    "resolve_slug_subject",
    "truncate_subject",
]
