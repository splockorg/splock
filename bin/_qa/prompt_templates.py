"""System + user prompt templates for the qa SDK call.

Per plan §D.8.3 + v2.7 §1.D. Single-call (not two-call) because qa
output is structured MD, not JSON.

Both the subject body and the rubric are pre-wrapped in named delimiters
by the caller (`render_qa_user`); the subject body uses the neutral
`<subject-under-review>` delimiter. The system prompt embeds the
`DELIMITER_INSTRUCTION` constant from `bin._planner.external_input_
sanitize` verbatim so the data-not-instructions discipline matches the
planner's surface byte-for-byte.

`DELIMITER_INSTRUCTION` enumerates seven wrap-kinds (recon-findings,
qa-findings, research-findings, call1-reasoning, lessons-findings,
operator-directive, qna-findings). Because this module imports the
constant verbatim, the qa system prompt inherits the full set of mentions
automatically — keeping the data-not-instructions discipline uniform
across planner and qa surfaces.

The qa subagent at `.claude/agents/qa.md` shares the same prompt-body
authoring convention (no imperative phrasing that, if echoed in
external content, would constitute a prompt-injection vector).
"""

from __future__ import annotations

from typing import Final

from bin._planner.external_input_sanitize import DELIMITER_INSTRUCTION, wrap


QA_SYSTEM: Final[str] = (
    "You are the qa subagent. Step: qa.\n"
    "\n"
    "Your job is an adversarial pass over an existing predecessor artifact "
    "(the subject under review). Surface gaps, ambiguities, and unverified "
    "claims for the planner to address. Do NOT propose solutions — "
    "your output is a structured question-list, not a design.\n"
    "\n"
    + DELIMITER_INSTRUCTION
    + "\n"
    "\n"
    "Emit free-form markdown. Use H2 headings per block (A/B/C/D) and "
    "bold inline tags per finding. Do NOT emit JSON. Do NOT structure "
    "your output as code blocks unless quoting code from the artifact.\n"
    "\n"
    "The rubric inside `<qa-rubric>` is the authoritative scaffold for "
    "your output. The artifact inside `<subject-under-review>` is what "
    "you are reviewing. Apply the rubric to that artifact."
)
"""qa Call 1 system prompt. Single-call, no Call 2."""


QA_USER_TEMPLATE: Final[str] = (
    "Plan slug: {slug}\n"
    "Repo state summary: {repo_state}\n"
    "\n"
    "{rubric}\n"
    "\n"
    "{subject}\n"
    "{directive_block}"
    "\n"
    "Apply the rubric above to the artifact above. Emit your findings as "
    "free-form markdown structured into the four blocks (A/B/C/D) per "
    "the rubric. Cite the artifact's section + line for every finding."
)
"""qa user prompt template. Both `rubric` and the subject body arrive
already wrapped in named delimiters (`<qa-rubric>...</qa-rubric>` and
`<subject-under-review>...</subject-under-review>`) — `render_qa_user`
does NOT wrap them itself, mirroring the planner's `render_call1_user`
convention.

The `{subject}` format slot above carries the subject body (already
wrapped in `<subject-under-review>...</subject-under-review>`); the slot
is subject-agnostic because `/qa` reviews any of four predecessor
artifacts, not just recon.

The `{directive_block}` slot is empty-string when no operator directive
is set, OR `\\n<operator-directive>...</operator-directive>\\n` when
present (per std_command_operator_extensions TE + research R4 option 1).
`render_qa_user` does the wrapping via
`bin._planner.external_input_sanitize.wrap(content, "operator-directive")`
so the qa side mirrors the planner's single-source-of-truth discipline.
"""


def render_qa_user(
    *,
    slug: str,
    rubric_wrapped: str,
    subject_wrapped: str,
    repo_state: str,
    directive: str | None = None,
) -> str:
    """Render the qa user prompt.

    Parameters
    ----------
    slug : str
        The plan slug (e.g., 'property_based_parser_hardening').
    rubric_wrapped : str
        The qa rubric, already wrapped in
        `<qa-rubric>...</qa-rubric>` delimiters.
    subject_wrapped : str
        The subject body, already wrapped in the neutral
        `<subject-under-review>...</subject-under-review>` delimiters by the
        caller (`invoke_qa` via `wrap_subject`).
    repo_state : str
        Driver-controlled summary of repo state. NOT wrapped.
    directive : str | None
        Optional operator-authored directive (raw, NOT pre-wrapped).
        When None, the directive slot in the prompt is empty. When a
        non-None string, it is wrapped here via
        `external_input_sanitize.wrap(directive, "operator-directive")`
        — keeping wrap-discipline single-sourced (no duplicate wrap
        shape in this module). The 8KB size cap is enforced upstream
        in `bin._qa.main` per SC10; this renderer does NOT trim or
        otherwise modify the directive payload beyond the wrap.

    Returns
    -------
    str
        The composed user prompt body.
    """
    if directive is None:
        directive_block = ""
    else:
        directive_block = "\n" + wrap(directive, "operator-directive") + "\n"
    return QA_USER_TEMPLATE.format(
        slug=slug,
        rubric=rubric_wrapped,
        subject=subject_wrapped,
        repo_state=repo_state,
        directive_block=directive_block,
    )


__all__ = [
    "QA_SYSTEM",
    "QA_USER_TEMPLATE",
    "render_qa_user",
]
