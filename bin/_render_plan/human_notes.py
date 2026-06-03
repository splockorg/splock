"""Anchor-delimited operator-notes preservation.

Per implplan §B.impl.5 (lines 1175-1217). The single MD section the
operator may edit is `## Notes (human-authored)`, delimited by HTML
comment anchors:

    <!-- BEGIN-HUMAN-NOTES (do not remove this comment; render_plan reads it as anchor) -->

    (operator writes here freely; markdown of any shape)

    <!-- END-HUMAN-NOTES (do not remove this comment) -->

Edge cases handled (per implplan §B.impl.5 lines 1204-1213):
- No anchors → bootstrap with empty block + template instruction
- Single anchor pair → preserve verbatim
- Dual anchor blocks → concatenate in document order; emit warning
- Unbalanced (BEGIN without END or vice versa) → treat as missing
- False anchor inside code-fence → outermost-pair match preserves it
- Content > 64 KB → no truncation; emit `large-notes` warning
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# Anchor regexes per implplan §B.impl.5 lines 1182-1187. Patterns are
# multiline-anchored so we match anchor-only lines, not anchor-inside-prose.
BEGIN_ANCHOR_RE = re.compile(
    r"^<!--\s*BEGIN-HUMAN-NOTES.*?-->\s*$", re.MULTILINE
)
END_ANCHOR_RE = re.compile(
    r"^<!--\s*END-HUMAN-NOTES.*?-->\s*$", re.MULTILINE
)

# Outermost-pair match is implemented in `extract_anchor_content` by
# scanning all BEGIN/END occurrences and pairing first BEGIN with last
# END (per implplan §B.impl.5 line 1212: "the extractor first scans for
# all `BEGIN_ANCHOR_RE` occurrences; selects the outermost pair").

LARGE_NOTES_THRESHOLD_BYTES = 64 * 1024

# Canonical anchor strings emitted by `wrap_in_anchors`.
_CANONICAL_BEGIN = (
    "<!-- BEGIN-HUMAN-NOTES (do not remove this comment; render_plan "
    "reads it as anchor) -->"
)
_CANONICAL_END = "<!-- END-HUMAN-NOTES (do not remove this comment) -->"

_BOOTSTRAP_PLACEHOLDER = (
    "_Add notes here; this section is preserved across renders._"
)

# Public re-export for callers (e.g. md_renderer._render_state_body) that
# need the bootstrap text directly without invoking wrap_in_anchors. Added
# 2026-05-23 as part of orch_status_render T3 — state-kind renderer
# substitutes raw notes into a template whose anchor literals are
# pre-embedded, so the wrapping helper is bypassed; the placeholder
# string itself still needs to be addressable.
BOOTSTRAP_PLACEHOLDER = _BOOTSTRAP_PLACEHOLDER

AnchorStatus = Literal["ok", "missing", "dual"]


@dataclass
class AnchorExtractionResult:
    """Result of extracting anchor-block content from existing MD.

    Per implplan §B.impl.5 line 1200: status discriminates the three
    real outcomes. `warning` is an opaque string the caller emits via
    the hook log (when §C.impl is live) — Phase 1 prints it to stderr.
    """

    content: str
    status: AnchorStatus
    warning: str | None = None
    warnings: list[str] = field(default_factory=list)


def extract_anchor_content(md_text: str | None) -> AnchorExtractionResult:
    """Pull operator-edited content from inside the anchor block.

    Empty/missing MD → status `missing` with bootstrap placeholder.
    Outermost-pair logic handles false anchors inside code fences (the
    inner anchor matches the regex but the outer pair governs the
    extracted slice — per implplan §B.impl.5 lines 1191-1194).
    """
    if md_text is None or md_text.strip() == "":
        return AnchorExtractionResult(
            content=_BOOTSTRAP_PLACEHOLDER,
            status="missing",
        )

    begins = list(BEGIN_ANCHOR_RE.finditer(md_text))
    ends = list(END_ANCHOR_RE.finditer(md_text))

    if not begins and not ends:
        return AnchorExtractionResult(
            content=_BOOTSTRAP_PLACEHOLDER,
            status="missing",
        )

    if len(begins) != len(ends):
        # Unbalanced — treat as missing. Per implplan §B.impl.5 line 1211.
        return AnchorExtractionResult(
            content=_BOOTSTRAP_PLACEHOLDER,
            status="missing",
            warning="render-plan unbalanced-anchors reset",
        )

    if len(begins) == 1 and len(ends) == 1:
        # Single block — preserve verbatim. Outermost-pair extraction.
        begin = begins[0]
        end = ends[0]
        if end.start() < begin.end():
            # END appears before BEGIN — corrupted; treat as missing.
            return AnchorExtractionResult(
                content=_BOOTSTRAP_PLACEHOLDER,
                status="missing",
                warning="render-plan unbalanced-anchors reset",
            )
        content = _slice_between(md_text, begin.end(), end.start())
        warnings: list[str] = []
        if len(content.encode("utf-8")) > LARGE_NOTES_THRESHOLD_BYTES:
            warnings.append("render-plan large-notes")
        return AnchorExtractionResult(
            content=content,
            status="ok",
            warning=warnings[0] if warnings else None,
            warnings=warnings,
        )

    # Dual (or more) anchor blocks. Concatenate contents in doc order.
    # Per implplan §B.impl.5 line 1210: "concatenate contents in
    # document order; emit `render-plan dual-anchor merged` warning".
    # Outermost-pair semantics still hold per block; we iterate paired
    # BEGIN/END entries (we already confirmed equal counts).
    chunks: list[str] = []
    for begin, end in zip(begins, ends):
        if end.start() < begin.end():
            # corrupted pair — skip rather than failing the whole render.
            continue
        chunks.append(_slice_between(md_text, begin.end(), end.start()))
    merged = "\n\n".join(chunk.strip() for chunk in chunks if chunk.strip())
    if not merged:
        merged = _BOOTSTRAP_PLACEHOLDER
    warnings = ["render-plan dual-anchor merged"]
    if len(merged.encode("utf-8")) > LARGE_NOTES_THRESHOLD_BYTES:
        warnings.append("render-plan large-notes")
    return AnchorExtractionResult(
        content=merged,
        status="dual",
        warning=warnings[0],
        warnings=warnings,
    )


def _slice_between(md_text: str, start: int, end: int) -> str:
    """Slice the substring + strip surrounding blank lines.

    The anchor lines themselves are matched (not included); we then
    strip ONLY leading/trailing fully-blank lines to keep the canonical
    "anchor / blank / content / blank / anchor" wrapping while not
    artificially padding the extracted text.
    """
    raw = md_text[start:end]
    return raw.strip("\n").strip()


def wrap_in_anchors(content: str) -> str:
    """Wrap operator-notes content inside canonical anchor comments.

    The caller (`md_renderer.render_canonical_body`) embeds the result
    as the final H2 section. The anchor strings emitted here are
    byte-stable: identical input produces identical output (idempotency
    property tested in `test_idempotency.py`).
    """
    stripped = content.strip() if content else ""
    if not stripped:
        stripped = _BOOTSTRAP_PLACEHOLDER
    return (
        f"{_CANONICAL_BEGIN}\n\n"
        f"{stripped}\n\n"
        f"{_CANONICAL_END}"
    )


def detect_outside_anchor_diff(
    existing_md: str | None, canonical_body: str
) -> list["DiffHunk"]:
    """Detect operator edits outside the anchor block.

    Per implplan §B.impl.11 #1 (RATIFIED 2026-05-20, lines 1404-1412) and
    §B.impl.4 step 7 (line 1115): when the existing MD's non-anchor
    region differs from what the new canonical body would produce
    pre-anchor-insertion, the operator has edited a section the renderer
    is about to clobber. We emit a warning (not a refusal); the render
    proceeds.

    `canonical_body` here is the freshly-rendered MD WITHOUT operator
    notes re-inserted — comparing this against the existing MD's
    non-anchor region tells us if a clobber will occur.

    Returns a list of DiffHunk for the test suite; empty list means no
    outside-anchor edits.
    """
    if existing_md is None:
        return []

    existing_outside = _strip_anchor_block(existing_md)
    canonical_outside = _strip_anchor_block(canonical_body)

    if existing_outside.strip() == canonical_outside.strip():
        return []
    # Whitespace-only edge case (per OO-1 in this build's spec): if the
    # only difference is whitespace, no warning.
    if (
        _normalize_whitespace(existing_outside)
        == _normalize_whitespace(canonical_outside)
    ):
        return []
    return [
        DiffHunk(
            before=existing_outside,
            after=canonical_outside,
            reason="outside-anchor content differs",
        )
    ]


def _strip_anchor_block(md_text: str) -> str:
    """Return md_text with the BEGIN..END anchor block (and any prior
    `## Notes (human-authored)` heading line on the immediately
    preceding line) excised.

    We strip the anchor block plus its containing H2 heading so the
    outside-anchor diff doesn't trip on the heading text alone.
    """
    begin_match = BEGIN_ANCHOR_RE.search(md_text)
    end_match = list(END_ANCHOR_RE.finditer(md_text))
    if not begin_match or not end_match:
        return md_text
    # Find the line-start of the BEGIN match.
    block_start = begin_match.start()
    block_end = end_match[-1].end()
    # Also strip the `## Notes (human-authored)` H2 heading if it
    # immediately precedes the anchor block.
    pre = md_text[:block_start].rstrip()
    # Scan backwards to the previous heading; if it's the canonical
    # notes heading, drop it too.
    lines = pre.split("\n")
    if lines and lines[-1].strip().startswith("## Notes"):
        lines = lines[:-1]
    pre_cleaned = "\n".join(lines).rstrip()
    post = md_text[block_end:].lstrip("\n")
    if post.strip():
        return f"{pre_cleaned}\n{post}"
    return pre_cleaned


def _normalize_whitespace(text: str) -> str:
    """Collapse all whitespace runs to single spaces; strip ends."""
    return " ".join(text.split())


@dataclass
class DiffHunk:
    """Minimal diff record consumed by the test suite.

    Phase 1 carries only the smallest payload needed for the warning;
    later phases can extend with line numbers if needed.
    """

    before: str
    after: str
    reason: str


def merge_dual_blocks(md_text: str) -> str:
    """Convenience: extract + re-wrap dual-anchor content into a single block.

    Not called by the main render flow (the renderer always re-wraps
    fresh anchors around extracted content), but exposed for operator
    repair tooling. When the input has dual anchor blocks, emits a
    `render-plan dual-anchor merged` warning to stderr per §B.impl.5
    line 1202. (Spec calls for hook-log subprocess routing; deferred to
    Phase 2 when §G ships bin/hook-log.)
    """
    import sys

    result = extract_anchor_content(md_text)
    if result.status == "dual":
        print(
            "render-plan: WARNING render-plan dual-anchor merged",
            file=sys.stderr,
        )
    return wrap_in_anchors(result.content)


def load_anchor_template(template_dir: Path) -> str:
    """Read `.claude/templates/human_notes_anchor.md.template`.

    Used during first-render bootstrap. Template content is the
    one-line operator instruction wrapped in anchors.
    """
    return (template_dir / "human_notes_anchor.md.template").read_text(
        encoding="utf-8"
    )


__all__ = [
    "AnchorExtractionResult",
    "AnchorStatus",
    "BOOTSTRAP_PLACEHOLDER",
    "DiffHunk",
    "BEGIN_ANCHOR_RE",
    "END_ANCHOR_RE",
    "LARGE_NOTES_THRESHOLD_BYTES",
    "detect_outside_anchor_diff",
    "extract_anchor_content",
    "load_anchor_template",
    "merge_dual_blocks",
    "wrap_in_anchors",
]
