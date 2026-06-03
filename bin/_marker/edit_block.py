"""Detail-file writer for closure_trigger markers (implplan §K.impl.6).

Per implplan §K.impl.6 (authoritative — supersedes plan §K.7 per
triage F-02): every `target=closure_trigger` marker MUST have a detail
file containing a `## Edit block (apply verbatim at next trigger)`
section, framed by HTML-comment anchors:

    <!-- BEGIN EDIT BLOCK (apply verbatim at next trigger) -->
    ## Edit block (apply verbatim at next trigger)

    > <verbatim text or diff content; preserved exactly>

    <!-- END EDIT BLOCK -->

`validate` refuses (exit 13) entries whose detail file is missing the
edit-block anchors or whose content between them is empty / whitespace-only.

`date` and `condition` markers do NOT require edit blocks; detail file
for them is operator-prose only.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Tuple


BEGIN_ANCHOR = "<!-- BEGIN EDIT BLOCK (apply verbatim at next trigger) -->"
END_ANCHOR = "<!-- END EDIT BLOCK -->"

# Permissive variants for legacy round-trip read. Validate refuses unless
# the canonical anchor pair is present.
BEGIN_ANCHOR_RE = re.compile(r"<!--\s*BEGIN\s+EDIT\s+BLOCK[^>]*-->", re.IGNORECASE)
END_ANCHOR_RE = re.compile(r"<!--\s*END\s+EDIT\s+BLOCK[^>]*-->", re.IGNORECASE)


def render_detail_file(
    marker_id: str,
    title: str,
    target: str,                  # raw trigger string (verbatim)
    source_plan: str,
    added_date: str,
    emitted_by: str,
    context: str,
    data_needed: str,
    edit_block_content: str = "",
    requires_edit_block: bool = True,
) -> str:
    """Render a detail-file body per implplan §K.impl.6 template.

    Note: `target` here is the **raw trigger string** (e.g.,
    `edit:foo.py:structural`) — not the schema enum. The trigger spec line
    in the template preserves the verbatim trigger for downstream consumers.
    """
    body = []
    body.append(f"# {marker_id} — {title}")
    body.append("")
    body.append(
        f"> Scheduled marker. Index entry: `docs/plans/scheduled_markers/list.md` § {marker_id}."
    )
    body.append("")
    body.append(f"**Target:** {target}")
    body.append(f"**Source plan:** {source_plan}")
    body.append(f"**Added:** {added_date}")
    body.append(f"**Emitted by:** {emitted_by}")
    body.append(f"**Context:** {context}")
    body.append("")
    body.append("## Data needed to close")
    body.append("")
    body.append(data_needed)
    body.append("")
    if requires_edit_block:
        body.append(BEGIN_ANCHOR)
        body.append("## Edit block (apply verbatim at next trigger)")
        body.append("")
        if edit_block_content:
            # Quote-prefix each line for verbatim preservation
            for line in edit_block_content.splitlines() or [edit_block_content]:
                body.append(f"> {line}" if line else ">")
        else:
            body.append("> _(edit content TBD — operator to fill before closure_trigger fires)_")
        body.append("")
        body.append(END_ANCHOR)
        body.append("")
    body.append("## How to close")
    body.append("")
    body.append(
        f"`bin/marker close {marker_id} --resolution \"applied edit at <commit-sha>\"`"
    )
    body.append("")
    return "\n".join(body) + "\n"


def extract_edit_block(detail_text: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract content between BEGIN/END EDIT BLOCK anchors.

    Returns (content_between_anchors, error_message). On success, error is None.
    On missing anchors or empty content, content is None and error is set.
    """
    begin_match = BEGIN_ANCHOR_RE.search(detail_text)
    end_match = END_ANCHOR_RE.search(detail_text)
    if begin_match is None or end_match is None:
        return None, "missing BEGIN/END EDIT BLOCK anchors"
    if end_match.start() < begin_match.end():
        return None, "END EDIT BLOCK appears before BEGIN EDIT BLOCK"
    content = detail_text[begin_match.end() : end_match.start()]
    stripped = content.strip()
    if not stripped:
        return None, "edit block content is empty / whitespace-only"
    # Strip the H2 header + any leading blank lines so callers see the
    # quoted-content only
    return content, None
