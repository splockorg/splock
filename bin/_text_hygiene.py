"""Mojibake tripwire for model-emitted text (the "glyph corruption" rough edge).

The planner puts model text on disk at exactly two seams: the Call-1
reasoning MD (``bin/_planner/main.py``) and the rendered MD twin
(``bin/_render_plan/main.py``). The plan JSON itself is immune — ``json.dumps``
defaults to ``ensure_ascii=True``, so every non-ASCII glyph is escaped to
``\\uXXXX`` and no encoding step can mangle it after the fact. Real corruption
therefore arrives as literal mojibake in the MODEL'S OWN emitted text (or a
mis-decoded pipe) and lands in the MD files operators have been proofreading
by hand. This module replaces the proofreading with a deterministic warning.

Detection is a closed marker list, not a decoder: the classic
UTF-8-bytes-read-as-CP1252 accidents all start with a small set of two-char
prefixes ("â€" for the smart-punctuation block, "â†" for arrows, "Â"/"Ã"
followed by Latin-1 punctuation or letters), and U+FFFD is wrong in any
emitted file. A closed list keeps false positives near zero — legitimate
English/Markdown text never contains "â€".

Detect + warn only, never rewrite: a wrong "fix" is worse than a loud
warning, and the JSON source of truth is unaffected either way.
"""

from __future__ import annotations

import sys
from typing import TextIO

#: Suspected-mojibake markers. Each entry is a literal substring whose
#: presence in emitted text is (in this codebase's language) always an
#: encoding accident, never intent.
_MOJIBAKE_MARKERS: tuple[str, ...] = (
    "�",  # U+FFFD replacement char — some decode already failed
    "â€",      # ' ' " " – — … • misdecoded (U+2013..U+2026 family)
    "â†",      # arrows → ← ↑ ↓ misdecoded
    "â‡",      # double arrows ⇒ ⇐
    "â‰", "âˆ",  # math: ≠ ≈ ∈ ∗
    "Â§", "Â·", "Â«", "Â»", "Â°", "Â±", "Â¶",  # Latin-1 punctuation with stray Â
    "Ã©", "Ã¨", "Ãª", "Ã¤", "Ã¶", "Ã¼", "Ã±", "Ã§",  # common accented letters
)

#: Cap on per-line stderr reports; a fully-mangled file should not scroll the
#: real signal away.
_MAX_REPORTED_LINES = 20


def find_mojibake(text: str) -> list[tuple[int, str, str]]:
    """Every suspected-mojibake occurrence as ``(line_number, marker, excerpt)``.

    Line numbers are 1-based. One tuple per (line, marker) pair — a line
    containing the same marker three times reports once.
    """
    findings: list[tuple[int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for marker in _MOJIBAKE_MARKERS:
            if marker in line:
                findings.append((lineno, marker, line.strip()[:120]))
    return findings


def warn_mojibake(text: str, source: str, stream: TextIO | None = None) -> int:
    """Report suspected mojibake in ``text`` to ``stream`` (default stderr).

    Returns the number of findings. Never raises; emitting the warning must
    not be able to fail the write it accompanies.
    """
    try:
        out = stream if stream is not None else sys.stderr
        findings = find_mojibake(text)
        for lineno, marker, excerpt in findings[:_MAX_REPORTED_LINES]:
            print(
                f"glyph-lint: {source}:{lineno}: suspected mojibake "
                f"{marker!r}: {excerpt}",
                file=out,
            )
        if len(findings) > _MAX_REPORTED_LINES:
            print(
                f"glyph-lint: {source}: ... and "
                f"{len(findings) - _MAX_REPORTED_LINES} more suspected line(s)",
                file=out,
            )
        return len(findings)
    except Exception:  # noqa: BLE001 — the tripwire must never break the emit
        return 0
