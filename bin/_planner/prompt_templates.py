"""Call 1 + Call 2 system + user prompt templates.

Per plan §D.3 / implplan §D.impl.3 lines 2706-2750. Two HTTP round-trips
per planning phase:

- Call 1 (Reasoning): NO `response_format`. Free-form MD scratchpad.
  Reasoning quality preserved; no constrained-decoding format pressure.
- Call 2 (Emission): `response_format={"type": "json_schema", ...}`.
  Transcribes Call 1's output verbatim into schema-valid JSON.

Both system prompts embed the `DELIMITER_INSTRUCTION` constant verbatim
(per `external_input_sanitize.py`), and the structure of the user prompts
ensures every external input (recon findings, qa findings, research
findings, lessons findings, Call 1's reasoning when re-entered at Call 2)
is wrapped in named `<...>` delimiters before submission.

Both prompts deliberately avoid load-bearing trust-the-model prose like
"MUST emit JSON only in second call" — plan §D.6 criterion 2 requires
enforcement to be structural, not prose-based. The single source of
structural enforcement is the driver in `two_call.py` making two
distinct SDK calls with different `response_format` configurations.
"""

from __future__ import annotations

from typing import Final

from .external_input_sanitize import DELIMITER_INSTRUCTION, wrap

# ----------------------------------------------------------------------
# tests_enabled contract (shared by Call 1 + Call 2, implplan step)
# ----------------------------------------------------------------------

TESTS_ENABLED_CONTRACT: Final[str] = (
    "tests_enabled contract (implplan only):\n"
    "  - Every tests_enabled entry MUST be a runnable pytest selector --\n"
    "    path/to/test_file.py or path/to/test_file.py::test_name -- whose\n"
    "    path component appears in the SAME task's file_paths_touched.\n"
    "    The typed gate command shape ('gate_cmd:' prefix; exit 0 = pass)\n"
    "    is RESERVED, not active -- do NOT author it; nothing executes it\n"
    "    (real_tests_at_junctions T6/SC3 narrowed decision).\n"
    "  - Prose describing testing intent belongs in that task's test_plan\n"
    "    entries, never in tests_enabled.\n"
    "  - An empty tests_enabled is allowed only for pure bookkeeping or\n"
    "    doc-only tasks; such a task SHOULD declare why via a\n"
    "    verification_kind exemption marker: a test_plan entry whose\n"
    "    test_id starts with 'verification_kind:' (e.g.\n"
    "    'verification_kind: artifact_review'), with asserts/fixture\n"
    "    describing the non-pytest verification."
)
"""The tests_enabled authoring contract (real_tests_at_junctions SC1).

Embedded in BOTH the Call 1 reasoning instructions and the Call 2
emission system prompt so the shape constraint survives transcription.
This is the soft, LLM-dependent layer; the deterministic twin is the
plan-time validator (SC2) in `bin/_verify_plan/strict.py`.

T6 (SC3) update: the typed-gate-command branch was NARROWED — the
`gate_cmd:` prefix is reserved recognition-only (the strict validator
rejects an authored entry as of the 2026-06-11 follow-up patch; the
junction-time classifier still recognizes but no gate executes it),
and the supported
convention for non-pytest tasks is the `verification_kind:` test_plan
exemption marker (`bin/_verify_plan/strict.VERIFICATION_KIND_MARKER_PREFIX`).
See `docs/plans/_closed/real_tests_at_junctions/typed_gate_command_decision.md`.

MUST NOT contain `{` or `}` — it is concatenated into templates that
later go through `.format(...)`, where stray braces would raise
KeyError at render time.
"""


# ----------------------------------------------------------------------
# Call 1 — Reasoning (no response_format)
# ----------------------------------------------------------------------

CALL1_SYSTEM: Final[str] = (
    "You are the planner subagent. Step: {step}. Tier: {tier}.\n"
    "\n"
    + DELIMITER_INSTRUCTION
    + "\n"
    "\n"
    "Emit your reasoning as free-form markdown. Use H2 headings per task or "
    "per success criterion. Do NOT emit JSON. Do NOT structure your output "
    "as code blocks unless quoting code.\n"
    "\n"
    "Take your time. Reasoning quality matters more here than output brevity."
)
"""Call 1 system prompt. `{step}` is 'plan' or 'implplan'; `{tier}` is the
work-sizing tier (e.g., 'Tier 2'). `.format(...)` is called at invocation
time by `two_call.py`."""


CALL1_USER_TEMPLATE: Final[str] = (
    "Inputs for planning phase:\n"
    "\n"
    "Plan slug: {slug}\n"
    "Repo state summary: {repo_state}\n"
    "\n"
    "{recon}\n"
    "\n"
    "{qa}\n"
    "\n"
    "{research}\n"
    "\n"
    "{qna}\n"
    "\n"
    "{lessons}\n"
    "{directive_block}"
    "\n"
    "{prior_plan_section}\n"
    "\n"
    "Instructions:\n"
    "  Decompose this planning problem into {plan_fields_or_tasks}.\n"
    "  For each entry, reason through:\n"
    "    - what failure modes the entry addresses\n"
    "    - what tests catch those failure modes (tests_enabled targets --\n"
    "      for implplan, name runnable selectors per the contract below,\n"
    "      and put the narrative testing intent in test_plan entries)\n"
    "    - what dependencies the entry has\n"
    "    - (implplan only) which file paths and call sites are touched\n"
    "  Emit your reasoning as free-form markdown.\n"
    "\n"
    + TESTS_ENABLED_CONTRACT
    + "\n"
)
"""Call 1 user prompt template. All external inputs are wrapped in named
delimiters BY THE CALLER (`render_call1_user`) before being slotted in;
this template just stitches the wrapped strings together.

The `{directive_block}` slot is empty-string when no operator directive is
set, OR `\\n<operator-directive>...</operator-directive>\\n` when present
(per std_command_operator_extensions TD + research R4 option 1).
`render_call1_user` does the wrapping via
`bin._planner.external_input_sanitize.wrap(content, "operator-directive")`
so the planner side mirrors the qa side's single-source-of-truth wrap
discipline.
"""


def render_call1_user(
    *,
    slug: str,
    recon: str,
    qa: str,
    research: str,
    lessons: str,
    repo_state: str,
    prior_plan: str | None,
    step: str,
    directive: str | None = None,
    qna: str = "",
) -> str:
    """Render the Call 1 user prompt.

    Parameters
    ----------
    slug : str
        The plan slug (e.g., 'brand_handoff_gate').
    recon : str
        Already-wrapped (`<recon-findings>...</recon-findings>`) recon
        findings string. Use `external_input_sanitize.wrap(...)` upstream.
    qa : str
        Already-wrapped QA findings.
    research : str
        Already-wrapped research findings.
    qna : str
        Already-wrapped (`<qna-findings>...`) Q&A-investigation findings
        (v2.8). Empty-string default for pre-v2.8 callers; two_call.py
        passes the wrapped `<slug>_qna.md` (+ variants) group. Rendered
        between the research and lessons blocks.
    lessons : str
        Already-wrapped lessons findings. May be empty string content
        (e.g., `<lessons-findings>\\n\\n</lessons-findings>`) if no prior
        lessons apply to this slug yet.
    repo_state : str
        Free-form summary of repo state (e.g., 'main @ commit abc123;
        active in-flight releases listed in docs/in_flight_releases.md').
        NOT wrapped — this is a driver-side controlled string.
    prior_plan : str | None
        The prior `<slug>_plan.json` content (already schema-validated by
        §B), fenced into the reasoning prompt so Call 1 reasons against the
        CURRENT plan. Supplied in TWO cases: the implplan step (always), and
        the `plan` step under `--amend` (plan_surgical_amend §SC6 / T6b — the
        existing plan is fed in so the patch is reasoned against it). None for
        a fresh `plan` run, which renders the `(no prior plan)` placeholder.
    step : str
        'plan' or 'implplan'. Used to decide between 'plan fields' and
        'task entries' phrasing.
    directive : str | None
        Optional operator-authored directive (raw, NOT pre-wrapped). When
        None, the directive slot in the prompt is empty. When a non-None
        string, it is wrapped here via
        `external_input_sanitize.wrap(directive, "operator-directive")`
        — keeping wrap-discipline single-sourced (no duplicate wrap
        shape in this module). The 8KB size cap is enforced upstream in
        `bin._planner.main` per SC10; this renderer does NOT trim or
        otherwise modify the directive payload beyond the wrap. Mirrors
        the qa-side `render_qa_user` directive contract.

    Returns
    -------
    str
        The composed user prompt body.
    """
    plan_fields_or_tasks = (
        "task entries (one H2 heading per task)"
        if step == "implplan"
        else "plan fields (one H2 heading per success criterion)"
    )

    if prior_plan is not None:
        prior_plan_section = (
            "Prior plan substrate (<slug>_plan.json, already schema-valid):\n"
            f"```json\n{prior_plan}\n```"
        )
    else:
        prior_plan_section = "(no prior plan; this is the plan step)"

    if directive is None:
        directive_block = ""
    else:
        directive_block = "\n" + wrap(directive, "operator-directive") + "\n"

    return CALL1_USER_TEMPLATE.format(
        slug=slug,
        recon=recon,
        qa=qa,
        research=research,
        qna=qna,
        lessons=lessons,
        repo_state=repo_state,
        prior_plan_section=prior_plan_section,
        plan_fields_or_tasks=plan_fields_or_tasks,
        directive_block=directive_block,
    )


# ----------------------------------------------------------------------
# Call 2 — Emission (response_format set)
# ----------------------------------------------------------------------

CALL2_SYSTEM: Final[str] = (
    "You are the planner subagent in emission mode. Step: {step}.\n"
    "\n"
    + DELIMITER_INSTRUCTION
    + "\n"
    "\n"
    "Transcribe the reasoning above into schema-valid JSON. Do not "
    "introduce new information; do not omit information present in the "
    "reasoning. The schema is authoritative; the reasoning is your "
    "source.\n"
    "\n"
    "When emitting an orchestrator (implplan step), one SHAPE rule applies "
    "at transcription time: if the reasoning describes a test in prose, "
    "place that prose in the task's test_plan entries and emit in "
    "tests_enabled only the runnable pytest selector or typed gate command "
    "it names.\n"
    "\n"
    + TESTS_ENABLED_CONTRACT
    + "\n"
    "\n"
    "Output via the structured output mechanism — your output_format is "
    "set; emit a single JSON document matching the schema."
)
"""Call 2 system prompt for the FULL-emission modes ('plan' / 'implplan').
`{step}` is 'plan' or 'implplan'. Transcribes Call 1's reasoning into a
single full plan/orchestrator JSON document.

Embeds `TESTS_ENABLED_CONTRACT` (real_tests_at_junctions SC1) so the
emission step preserves selector shape rather than transcribing Call 1
test prose verbatim into `tests_enabled`."""


CALL2_SYSTEM_PATCH: Final[str] = (
    "You are the planner subagent in emission mode (surgical amend). "
    "Step: {step}.\n"
    "\n"
    + DELIMITER_INSTRUCTION
    + "\n"
    "\n"
    "A prior plan was supplied in the reasoning above. Do NOT re-emit the "
    "whole plan. Emit a SURGICAL PATCH: an ordered op-list of the MINIMAL "
    "changes needed to fold the amend directive into that prior plan.\n"
    "\n"
    "Each op addresses an existing plan entry BY KEY (it does not restate "
    "untouched entries):\n"
    "  - op_kind is one of exactly: success_criterion, task, component, "
    "reference, non_goal, scalar.\n"
    "  - action is one of exactly: replace, add, remove.\n"
    "  - address resolves the target entry in the prior plan: `id` for "
    "success_criterion/task, `name` for component, `kind`+`pointer` for "
    "reference, `index` for non_goal, `field` for scalar.\n"
    "  - value carries the replacement/new content for replace/add; OMIT "
    "value for remove. A replace/add value is a COMPLETE entry, never a "
    "partial diff: it MUST carry every field that op-kind's entries require, "
    "or the amend is refused before it applies. Required value fields per "
    "op-kind: success_criterion needs id and criterion; task needs id, title "
    "and depends_on (an array, possibly empty); component needs name, purpose "
    "and dependencies (an array, possibly empty); reference needs kind and "
    "pointer; non_goal and scalar take a single string value.\n"
    "\n"
    "A replace FULLY OVERWRITES the addressed entry — the value you emit "
    "becomes that entry verbatim. Reproduce the entry's existing content and "
    "apply ONLY the change the directive calls for; do NOT regenerate an entry "
    "from scratch or silently drop its non-targeted content unless the "
    "directive explicitly says to rewrite it wholesale.\n"
    "\n"
    "Keep the op-list as small as the directive allows — touch only the "
    "entries the directive actually changes; leave everything else "
    "unaddressed. Do not introduce information absent from the reasoning, "
    "and do not omit a change the reasoning calls for.\n"
    "\n"
    "Output via the structured output mechanism — your output_format is "
    "set to the patch schema; emit a single JSON patch document (a "
    "patch_version + ops array), NOT a full plan."
)
"""Call 2 system prompt for the PATCH-emission mode (`--amend`,
plan_surgical_amend §SC6 / T6c). `{step}` is the planning step ('plan').

Unlike `CALL2_SYSTEM`, this instructs the model to transcribe Call 1's
reasoning into a KEYED OP-LIST against the prior plan rather than a full
plan re-emission. The closed `op_kind` / `action` enums and the per-op-kind
addressing contract mirror `schemas/plan_patch_v1.schema.json` (the schema
the SDK enforces structurally; this prose only orients the model — the
schema is authoritative, per the §D criterion-2 structural-enforcement
rule). `bin/_planner/patch_apply.py` (T2/T3) applies the emitted op-list.

The schema itself is wired into Call 2's `output_config.format.schema` by
the third schema-selection branch in `invoke_planner` (T6d). T6c provides
this prompt plus `select_call2_system` so T6d can pair the patch schema
with the patch prompt."""


def select_call2_system(*, amend: bool) -> str:
    """Select the Call-2 system prompt variant for the emission mode.

    Single source of the patch-vs-full Call-2 system-prompt choice, kept in
    this module (not in `two_call.py`) so the prompt and its selector live
    together. `invoke_planner`'s third schema-selection branch (T6d) calls
    this to pair the patch-mode system prompt with `PLAN_PATCH_SCHEMA_V1`.

    Parameters
    ----------
    amend : bool
        True for the `--amend` patch-emission path (plan_surgical_amend
        §SC6): returns `CALL2_SYSTEM_PATCH` so Call 2 emits a keyed op-list
        against the prior plan. False for the ordinary full-emission
        plan/implplan path: returns `CALL2_SYSTEM` so Call 2 emits a full
        plan/orchestrator document. Keyword-only to force an explicit,
        readable call site.

    Returns
    -------
    str
        The unformatted system-prompt template (`{step}` slot intact — the
        caller `.format(step=...)`s it, exactly as for `CALL2_SYSTEM`).
    """
    return CALL2_SYSTEM_PATCH if amend else CALL2_SYSTEM


CALL2_USER_TEMPLATE: Final[str] = (
    "Schema (authoritative):\n"
    "```json\n{schema_inline}\n```\n"
    "\n"
    "{call1_wrapped}\n"
    "\n"
    "Transcribe the reasoning above into a single JSON document matching "
    "the schema."
)
"""Call 2 user prompt template. `call1_wrapped` MUST be already wrapped
in `<call1-reasoning>...</call1-reasoning>` delimiters by the caller."""


def render_call2_user(*, schema_inline: str, call1_wrapped: str) -> str:
    """Render the Call 2 user prompt.

    Parameters
    ----------
    schema_inline : str
        The schema fragment as a JSON string (typically
        `json.dumps(schema, indent=2)`). Embedded inline so the model
        sees the constraint surface even though the SDK enforces it
        mechanically at the structured-output layer.
    call1_wrapped : str
        Call 1's free-form MD output, already wrapped via
        `external_input_sanitize.wrap(call1_text, "call1-reasoning")`.

    Returns
    -------
    str
        The composed user prompt body.
    """
    return CALL2_USER_TEMPLATE.format(
        schema_inline=schema_inline,
        call1_wrapped=call1_wrapped,
    )


__all__ = [
    "CALL1_SYSTEM",
    "CALL1_USER_TEMPLATE",
    "CALL2_SYSTEM",
    "CALL2_SYSTEM_PATCH",
    "CALL2_USER_TEMPLATE",
    "TESTS_ENABLED_CONTRACT",
    "render_call1_user",
    "render_call2_user",
    "select_call2_system",
]
