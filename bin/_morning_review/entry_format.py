"""Daily-queue entry parser + renderer (implplan §H.impl.4).

The SOLE owner of the per-entry shape per the §H.impl.4 spec. Both
`bin/morning-review` (all subcommands) and the §F.impl.7 halt-handoff
caller import the renderer here (the halt-handoff caller imports
`render_entry(...)` and appends via its own atomic-write path).

Per-entry shape (matches plan §H.3 lines 2671-2702 verbatim):

```
## <task_id> (status: deferred since <HH:MM:SS>Z)

**Plan:** <slug>
**Chain ID:** <chain_id>
**Phase:** <N>
**Deferral reason:** <closed-enum value + freeform tail>
**Retry count when deferred:** <int>
**Verifier verdict ref:** verification/<sess_id>/<task_id>_<iso8601>.json
**Last verifier reasoning:**
```
> <verbatim verifier R1-R5 prose, line-prefixed with `> `>
```

**Operator triage:** [pending]

Options (run one):
  - bin/morning-review reactivate <slug> <task_id> [--reason <text>]
  - bin/morning-review route-outstanding <slug> <task_id> [--reason <text>]
  - bin/morning-review route-marker <slug> <task_id> --prefix <P> [--reason <text>]
  - bin/morning-review abandon <slug> <task_id> --confirm --reason <text>

---
```

Closed-enum mirror values per §H.impl.4:
  `[pending]` → `[reactivated]` / `[routed-outstanding]` / `[routed-marker]` / `[abandoned]`

Closed-enum `deferral_reason` values per §H.impl.4:
  retry_exceeded / tampering_detected /
  phase_boundary_review_exhausted / collision_detected (v1.5)

Renderer property: `render(parse(text)) == text` byte-for-byte
(regression test in `test_entry_format.py`).
"""

from __future__ import annotations

import dataclasses
import re
from typing import Iterable, List, Optional, Tuple


# --- Closed enums ----------------------------------------------------------

DEFERRAL_REASONS: frozenset[str] = frozenset(
    {
        "retry_exceeded",
        "tampering_detected",
        "phase_boundary_review_exhausted",
        "collision_detected",
    }
)
"""Per §H.impl.4 closed enum. Adding new values is a v1.5-class additive
bump per §C.impl.13 schema-bump policy applied to a §H closed-enum."""

TRIAGE_MIRRORS: frozenset[str] = frozenset(
    {
        "[pending]",
        "[reactivated]",
        "[routed-outstanding]",
        "[routed-marker]",
        "[abandoned]",
    }
)
"""Closed-enum mirror values per §H.impl.4 table."""

TERMINAL_MIRRORS: frozenset[str] = frozenset(
    {
        "[reactivated]",
        "[routed-outstanding]",
        "[routed-marker]",
        "[abandoned]",
    }
)

# Generator citation enum per §H.impl.4 header spec.
GENERATOR_HALT_HANDOFF = "chain driver"  # "chain driver <chain_id>"
GENERATOR_BOOTSTRAP = "bin/morning-review --internal-bootstrap-day"


# --- Errors ----------------------------------------------------------------

class UnknownDeferralReasonError(ValueError):
    """Raised on parser-load when a `Deferral reason:` value's leading
    token is not in DEFERRAL_REASONS."""


class MalformedEntryError(ValueError):
    """Raised when an entry is structurally invalid (missing required
    field, bad mirror value, etc.). Per §H.impl.4: the parser does NOT
    raise on a malformed entry; instead it emits a warning to hook log
    and skips. This exception is reserved for `parse_strict(...)`."""


# --- Data classes ----------------------------------------------------------

@dataclasses.dataclass
class Entry:
    """One morning-review queue entry."""

    task_id: str
    """Task identifier matching `T[0-9]+`."""

    status_since: str
    """`<HH:MM:SS>Z` of the original `wip → deferred` transition."""

    slug: str
    """Plan slug — matches the directory name."""

    chain_id: str
    """Chain ID of the run that deferred the task."""

    phase: int
    """Phase number (1-5) of the deferral."""

    deferral_reason_token: str
    """Leading token (DEFERRAL_REASONS closed enum)."""

    deferral_reason_tail: str
    """Freeform suffix after the closed-enum token (may be empty)."""

    retry_count: int
    """Retry counter when deferred."""

    verifier_verdict_ref: str
    """Relative path to the verifier JSON (or empty string if absent)."""

    verifier_reasoning: str
    """Verbatim verifier R1-R5 prose (without `> ` blockquote prefix)."""

    triage_mirror: str
    """One of TRIAGE_MIRRORS."""

    collision_id: Optional[str] = None
    """Optional — populated only when deferral_reason_token == 'collision_detected'."""

    lineage_snapshot: Optional[str] = None
    """Optional — JSON blob string populated for collision_detected entries."""

    trailing_notes: str = ""
    """Operator notes between the `---` terminator and the next H2
    (preserved on round-trip)."""


# --- Header rendering ------------------------------------------------------

def render_header(date_iso: str, emitter: str) -> str:
    """Render the date-stamped daily-file header per §H.impl.4.

    `emitter` is the closed-enum generator citation:
      - `chain driver <chain_id>` (halt-handoff)
      - `bin/morning-review --internal-bootstrap-day` (cold-start)
    """
    return (
        f"# Morning Review — {date_iso}\n"
        f"\n"
        f"Generated by {emitter}. Operator triage required.\n"
        f"\n"
        f"---\n"
    )


# --- Entry rendering -------------------------------------------------------

# Triple-backtick fence with optional language tag.
_FENCE = "```"


def render_entry(
    *,
    task_id: str,
    status_since: str,
    slug: str,
    chain_id: str,
    phase: int,
    deferral_reason: str,
    retry_count: int,
    verifier_verdict_ref: str,
    verifier_reasoning: str,
    triage_mirror: str = "[pending]",
    collision_id: Optional[str] = None,
    lineage_snapshot: Optional[str] = None,
    trailing_notes: str = "",
) -> str:
    """Render one entry to the canonical H.impl.4 shape.

    `deferral_reason` is a single string: the closed-enum token, optionally
    followed by a single space + freeform tail.

    `verifier_reasoning` is the verbatim verifier prose; this function
    prefixes each line with `> ` to render as a markdown blockquote.

    `trailing_notes` is appended after the per-entry `---` separator, before
    the next entry; it preserves operator scratch on round-trip.
    """
    if triage_mirror not in TRIAGE_MIRRORS:
        raise MalformedEntryError(
            f"triage_mirror={triage_mirror!r} not in TRIAGE_MIRRORS {sorted(TRIAGE_MIRRORS)}"
        )
    token, _, _ = deferral_reason.partition(" ")
    if token not in DEFERRAL_REASONS:
        raise UnknownDeferralReasonError(
            f"deferral_reason leading token {token!r} not in closed enum "
            f"{sorted(DEFERRAL_REASONS)}"
        )

    quoted = _blockquote(verifier_reasoning)
    options = _render_options(slug, task_id)

    lines: list[str] = []
    lines.append(f"## {task_id} (status: deferred since {status_since})")
    lines.append("")
    lines.append(f"**Plan:** {slug}")
    lines.append(f"**Chain ID:** {chain_id}")
    lines.append(f"**Phase:** {phase}")
    lines.append(f"**Deferral reason:** {deferral_reason}")
    lines.append(f"**Retry count when deferred:** {retry_count}")
    lines.append(f"**Verifier verdict ref:** {verifier_verdict_ref}")
    lines.append("**Last verifier reasoning:**")
    lines.append("")
    lines.append(quoted)
    lines.append("")
    lines.append(f"**Operator triage:** {triage_mirror}")
    lines.append("")
    lines.append("Options (run one):")
    lines.extend(options)
    if collision_id is not None:
        lines.append("")
        lines.append(f"**Collision ID:** {collision_id}")
    if lineage_snapshot is not None:
        lines.append("")
        lines.append(f"**Lineage snapshot:** {lineage_snapshot}")
    lines.append("")
    lines.append("---")
    out = "\n".join(lines) + "\n"
    if trailing_notes:
        # `trailing_notes` is included verbatim — caller responsibility
        # to ensure it ends with a newline. We do NOT add one.
        out += trailing_notes
    return out


def _render_options(slug: str, task_id: str) -> list[str]:
    """The four operator-action options per §H.impl.4 spec."""
    return [
        f"  - bin/morning-review reactivate {slug} {task_id} [--reason <text>]",
        f"  - bin/morning-review route-outstanding {slug} {task_id} [--reason <text>]",
        f"  - bin/morning-review route-marker {slug} {task_id} --prefix <P> [--reason <text>]",
        f"  - bin/morning-review abandon {slug} {task_id} --confirm --reason <text>",
    ]


def _blockquote(text: str) -> str:
    """Line-prefix every line with `> ` to render as markdown blockquote.

    Empty lines become `>` (no trailing space) per CommonMark blockquote
    rules; non-empty lines become `> <line>`.
    """
    if not text:
        return ">"
    out_lines = []
    for line in text.splitlines():
        if line:
            out_lines.append(f"> {line}")
        else:
            out_lines.append(">")
    return "\n".join(out_lines)


def _unblockquote(text: str) -> str:
    """Strip `> ` prefix from each line of a blockquote (inverse of
    `_blockquote`). Tolerates `>` (no trailing space) for empty lines."""
    out_lines = []
    for line in text.splitlines():
        if line.startswith("> "):
            out_lines.append(line[2:])
        elif line == ">":
            out_lines.append("")
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


# --- Parser ----------------------------------------------------------------

# H2 boundary: `## <task_id> (status: ...)`. Task IDs match `T[0-9]+`.
_H2_RE = re.compile(
    r"^## (?P<task_id>T[0-9]+) \(status: deferred since (?P<status_since>[^)]+)\)$"
)
_FIELD_RES = {
    "slug": re.compile(r"^\*\*Plan:\*\* (?P<v>.*)$"),
    "chain_id": re.compile(r"^\*\*Chain ID:\*\* (?P<v>.*)$"),
    "phase": re.compile(r"^\*\*Phase:\*\* (?P<v>.*)$"),
    "deferral_reason": re.compile(r"^\*\*Deferral reason:\*\* (?P<v>.*)$"),
    "retry_count": re.compile(r"^\*\*Retry count when deferred:\*\* (?P<v>.*)$"),
    "verifier_verdict_ref": re.compile(r"^\*\*Verifier verdict ref:\*\* (?P<v>.*)$"),
    "triage_mirror": re.compile(r"^\*\*Operator triage:\*\* (?P<v>\[[^\]]*\])$"),
    "collision_id": re.compile(r"^\*\*Collision ID:\*\* (?P<v>.*)$"),
    "lineage_snapshot": re.compile(r"^\*\*Lineage snapshot:\*\* (?P<v>.*)$"),
}


@dataclasses.dataclass
class _ParsedBlock:
    """Internal: the raw lines of one entry's H2-bounded region."""

    h2_line: str
    body_lines: list[str]
    trailing_notes_lines: list[str]


def _split_entries(text: str) -> Tuple[str, List[_ParsedBlock]]:
    """Split the daily-file text into (header, list-of-entry-blocks).

    The header is everything before the first H2. Each entry block runs
    from its `##` line through (and including) the `---` terminator;
    trailing operator notes between that `---` and the next `##` (or EOF)
    are preserved as `trailing_notes_lines`.
    """
    lines = text.splitlines(keepends=False)
    # Find first H2.
    i = 0
    while i < len(lines) and not _H2_RE.match(lines[i]):
        i += 1
    header = "\n".join(lines[:i])
    if i > 0:
        # Preserve trailing newline if the slice ends at a line boundary.
        header += "\n"

    blocks: list[_ParsedBlock] = []
    while i < len(lines):
        if not _H2_RE.match(lines[i]):
            # Stray lines between blocks (shouldn't happen post-split);
            # absorb into prior block's trailing_notes.
            if blocks:
                blocks[-1].trailing_notes_lines.append(lines[i])
            i += 1
            continue
        h2 = lines[i]
        i += 1
        body: list[str] = []
        # Collect until we see a `---` on its own line marking the entry
        # terminator. Trailing notes follow until the next H2 or EOF.
        while i < len(lines) and lines[i] != "---":
            body.append(lines[i])
            i += 1
        # Include the `---` line itself in body for byte-stable round-trip.
        if i < len(lines) and lines[i] == "---":
            body.append(lines[i])
            i += 1
        # Trailing notes: everything until next H2.
        notes: list[str] = []
        while i < len(lines) and not _H2_RE.match(lines[i]):
            notes.append(lines[i])
            i += 1
        blocks.append(
            _ParsedBlock(
                h2_line=h2,
                body_lines=body,
                trailing_notes_lines=notes,
            )
        )
    return header, blocks


def _parse_block(block: _ParsedBlock) -> Entry:
    """Parse one _ParsedBlock into an Entry. Raises MalformedEntryError on
    structural failure."""
    m = _H2_RE.match(block.h2_line)
    if not m:
        raise MalformedEntryError(f"H2 line does not match pattern: {block.h2_line!r}")
    task_id = m.group("task_id")
    status_since = m.group("status_since")

    fields: dict[str, str] = {}
    # Detect the blockquote: lines starting with `>` between
    # "Last verifier reasoning:" and the next bold field.
    # State values: "pre-heading" (default), "after-heading-leading-blanks"
    # (we just saw the heading and may see one or more blank lines before
    # the quote starts), "in-quote" (collecting `>`-prefixed lines).
    quote_state = "pre-heading"
    quote_lines: list[str] = []
    for line in block.body_lines:
        if line == "**Last verifier reasoning:**":
            quote_state = "after-heading-leading-blanks"
            continue
        if quote_state == "after-heading-leading-blanks":
            if line == "":
                # Structural blank between the heading and the quote — skip
                # it; do NOT accumulate into quote_lines (the renderer
                # re-inserts the structural blank on its own).
                continue
            if line.startswith(">"):
                quote_state = "in-quote"
                quote_lines.append(line)
                continue
            # Non-quote, non-blank line: there is no quote content at all.
            # Fall through to field detection.
            quote_state = "post-quote"
        elif quote_state == "in-quote":
            if line.startswith(">"):
                quote_lines.append(line)
                continue
            if line == "":
                # Could be a structural blank between the quote and the
                # next field. Hold it without committing; if the very next
                # line is also `>`, we promote it back into the quote (a
                # mid-quote blank). Simplest deterministic shape: drop
                # trailing blanks when transitioning out.
                quote_state = "post-quote"
                # Drop any trailing blanks that may have crept in.
                while quote_lines and quote_lines[-1] == "":
                    quote_lines.pop()
                continue
            # Non-quote, non-blank line: end of blockquote.
            quote_state = "post-quote"
            while quote_lines and quote_lines[-1] == "":
                quote_lines.pop()
            # Fall through to field detection.
        for key, regex in _FIELD_RES.items():
            fm = regex.match(line)
            if fm:
                fields[key] = fm.group("v")
                break

    required = (
        "slug",
        "chain_id",
        "phase",
        "deferral_reason",
        "retry_count",
        "verifier_verdict_ref",
        "triage_mirror",
    )
    for key in required:
        if key not in fields:
            raise MalformedEntryError(
                f"entry {task_id}: missing required field {key!r}"
            )

    try:
        phase_int = int(fields["phase"])
    except ValueError as exc:
        raise MalformedEntryError(
            f"entry {task_id}: phase {fields['phase']!r} not an int"
        ) from exc
    try:
        retry_int = int(fields["retry_count"])
    except ValueError as exc:
        raise MalformedEntryError(
            f"entry {task_id}: retry_count {fields['retry_count']!r} not an int"
        ) from exc

    mirror = fields["triage_mirror"]
    if mirror not in TRIAGE_MIRRORS:
        raise MalformedEntryError(
            f"entry {task_id}: triage_mirror {mirror!r} not in closed enum"
        )

    deferral = fields["deferral_reason"]
    token, _, tail = deferral.partition(" ")
    if token not in DEFERRAL_REASONS:
        raise UnknownDeferralReasonError(
            f"entry {task_id}: deferral_reason leading token {token!r} "
            f"not in closed enum {sorted(DEFERRAL_REASONS)}"
        )

    verifier_reasoning = _unblockquote("\n".join(quote_lines)) if quote_lines else ""

    trailing = "\n".join(block.trailing_notes_lines)
    if block.trailing_notes_lines:
        # Preserve trailing-newline shape: original text always ended each
        # line with `\n` so we re-emit the same.
        trailing += "\n"

    return Entry(
        task_id=task_id,
        status_since=status_since,
        slug=fields["slug"],
        chain_id=fields["chain_id"],
        phase=phase_int,
        deferral_reason_token=token,
        deferral_reason_tail=tail,
        retry_count=retry_int,
        verifier_verdict_ref=fields["verifier_verdict_ref"],
        verifier_reasoning=verifier_reasoning,
        triage_mirror=mirror,
        collision_id=fields.get("collision_id"),
        lineage_snapshot=fields.get("lineage_snapshot"),
        trailing_notes=trailing,
    )


def parse(text: str, *, warn_hook=None) -> List[Entry]:
    """Parse daily-file text into a list of Entry.

    Per §H.impl.4: malformed entries do NOT crash; they emit a warning via
    `warn_hook(msg)` (default: send to `bin/hook-log morning-review
    parse-malformed`) and are skipped.

    `warn_hook(msg)` may be passed as a no-op for quiet test runs.
    """
    _, blocks = _split_entries(text)
    out: list[Entry] = []
    for block in blocks:
        try:
            out.append(_parse_block(block))
        except (MalformedEntryError, UnknownDeferralReasonError) as exc:
            if warn_hook is None:
                _default_warn_hook(str(exc))
            else:
                warn_hook(str(exc))
            continue
    return out


def parse_strict(text: str) -> List[Entry]:
    """Parse daily-file text; raise on any malformed entry."""
    _, blocks = _split_entries(text)
    return [_parse_block(b) for b in blocks]


def render_all(header: str, entries: Iterable[Entry]) -> str:
    """Render `header` + each entry (with its trailing notes). Inverse of
    `parse` for the byte-stable round-trip property."""
    out = [header]
    for entry in entries:
        deferral = entry.deferral_reason_token
        if entry.deferral_reason_tail:
            deferral = f"{entry.deferral_reason_token} {entry.deferral_reason_tail}"
        out.append(
            render_entry(
                task_id=entry.task_id,
                status_since=entry.status_since,
                slug=entry.slug,
                chain_id=entry.chain_id,
                phase=entry.phase,
                deferral_reason=deferral,
                retry_count=entry.retry_count,
                verifier_verdict_ref=entry.verifier_verdict_ref,
                verifier_reasoning=entry.verifier_reasoning,
                triage_mirror=entry.triage_mirror,
                collision_id=entry.collision_id,
                lineage_snapshot=entry.lineage_snapshot,
                trailing_notes=entry.trailing_notes,
            )
        )
    return "".join(out)


def _default_warn_hook(msg: str) -> None:
    """Default malformed-entry warning sink.

    Per §H.impl.4: emits to `bin/hook-log morning-review parse-malformed`.
    Silently drops if hook-log binary isn't available (e.g., test env);
    `parse` callers can override with their own `warn_hook`.
    """
    import subprocess
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    hook_log = repo_root / "bin" / "hook-log"
    if not hook_log.exists():
        return
    try:
        subprocess.run(
            [str(hook_log), "morning-review", "parse-malformed", msg],
            cwd=str(repo_root),
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


# --- LLM-consumed view helper ----------------------------------------------

def extract_for_llm(entry: Entry) -> str:
    """Return the entry's verifier-reasoning content wrapped in the §C.impl.7
    `<external-content>` delimiter for LLM-consumed views.

    Per §H.impl.4: the daily-file form itself does NOT wrap; the wrap is in
    the aggregator's extracted view only.
    """
    return (
        f"<external-content kind=\"verifier_reasoning\" "
        f"task_id=\"{entry.task_id}\" slug=\"{entry.slug}\">\n"
        f"{entry.verifier_reasoning}\n"
        f"</external-content>"
    )


# --- Mirror-update mutator (used by triage_dispatch + main.py) -------------

def update_triage_mirror(
    daily_text: str, task_id: str, new_mirror: str
) -> Tuple[str, Optional[Entry]]:
    """Return (new_text, matched_entry) with the matched task_id's mirror
    line updated to `new_mirror`.

    Returns (daily_text_unchanged, None) when no matching entry is found.
    Per §H.impl.6 step 1: the parser is the SOLE writer of mirror-line
    updates; `triage_dispatch.py` calls this then atomic-writes the result.
    """
    if new_mirror not in TRIAGE_MIRRORS:
        raise MalformedEntryError(
            f"new_mirror={new_mirror!r} not in closed enum {sorted(TRIAGE_MIRRORS)}"
        )
    header, blocks = _split_entries(daily_text)
    matched: Optional[Entry] = None
    for block in blocks:
        m = _H2_RE.match(block.h2_line)
        if not m:
            continue
        if m.group("task_id") != task_id:
            continue
        # Found the entry; rewrite its `Operator triage:` line in-place.
        new_body: list[str] = []
        replaced = False
        for line in block.body_lines:
            if not replaced and line.startswith("**Operator triage:**"):
                new_body.append(f"**Operator triage:** {new_mirror}")
                replaced = True
            else:
                new_body.append(line)
        block.body_lines = new_body
        try:
            matched = _parse_block(block)
        except (MalformedEntryError, UnknownDeferralReasonError):
            matched = None
        break
    if matched is None:
        return daily_text, None
    # Re-assemble the file: header + each block.
    out = [header]
    for block in blocks:
        out.append(block.h2_line)
        out.append("\n")
        if block.body_lines:
            out.append("\n".join(block.body_lines))
            out.append("\n")
        if block.trailing_notes_lines:
            out.append("\n".join(block.trailing_notes_lines))
            out.append("\n")
    return "".join(out), matched


def find_entry(daily_text: str, task_id: str) -> Optional[Entry]:
    """Return the Entry matching `task_id`, or None if absent / malformed."""
    for entry in parse(daily_text, warn_hook=lambda _msg: None):
        if entry.task_id == task_id:
            return entry
    return None


__all__ = [
    "DEFERRAL_REASONS",
    "TRIAGE_MIRRORS",
    "TERMINAL_MIRRORS",
    "GENERATOR_HALT_HANDOFF",
    "GENERATOR_BOOTSTRAP",
    "UnknownDeferralReasonError",
    "MalformedEntryError",
    "Entry",
    "render_header",
    "render_entry",
    "render_all",
    "parse",
    "parse_strict",
    "extract_for_llm",
    "update_triage_mirror",
    "find_entry",
]
