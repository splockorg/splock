"""Deterministic CLI-driven briefing construction.

Per splock plan §F.0 [NOVEL] caveat + §F.9.2 (briefing pipeline)
+ implplan §F.impl.6 (deterministic prompt construction).

**[NOVEL] caveat (carried verbatim from plan).** The deterministic-script
verifier prompt construction — driver constructs the runtime reviewer
prompt from CLI output (test stdout/stderr + git diff + hook flag +
plan/orchestrator JSON), structurally excluding any agent-authored
narrative — is a NOVEL pattern per research_findings_v1.md §H. No
standardized peer in verifier/judge literature at v2.7 ship time.
Re-evaluate when (a) a public peer emerges, (b) field-experience signal
warrants revision, or (c) a documented case forces a change.

Load-bearing contract — anchor §4a.3 element 3
----------------------------------------------

The function `build_briefing(...)` is the SOLE entry point for runtime
reviewer prompt construction. Its signature accepts ONLY CLI-derived
inputs: slug, iteration number, rubric kind, plus paths to test runner
output / diff / hook flag / plan + orchestrator substrate. There is
NO parameter that accepts an agent-authored narrative, Opus session
transcript, or coder-authored prose.

The runtime reviewer subagent (§D's `.claude/agents/reviewer.md`)
receives this briefing as a fully-formed prompt; the subagent does
NOT compose the prompt. This is the structural exclusion mechanism
that makes "no agent narrative" mechanical rather than prose-only.

Distinction from build-time orchestrator review junctions
---------------------------------------------------------

This module ships the RUNTIME path. The BUILD-TIME orchestrator §5
Sonnet review junctions (that built this very module) used a separate
agent-authored review framework — orchestrator anchor §4a.3 explicitly
disambiguates the two and forbids dual-purpose code paths. Inside
this module: only the runtime path is implemented; no build-time
hooks; no orchestrator §5 references in the assembly steps.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import rubric as rubric_mod

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Source-input sentinels — closed list per §F.impl.6
# ----------------------------------------------------------------------

#: Filename pattern for test runner stdout/stderr capture per iteration.
TEST_OUTPUT_FILENAME_TEMPLATE = "_test_output_iter{n}.txt"

#: Filename for PostToolUse `chain-test-file-edit-flag` staging.
HOOK_FLAG_FILENAME_TEMPLATE = "_sonnet_input_iter{n}_test_edits.jsonl"

#: Debug-echo target for `OVERNIGHT_DEBUG_RETRY_PROMPT=1`.
DEBUG_PROMPT_FILENAME_TEMPLATE = "_sonnet_prompt_iter{n}.txt"


# Sentinel string emitted when a hook-flag file is absent (no test-file
# edits during the iteration). NOT empty string — Sonnet must see the
# explicit "no edits" signal so R4 can confidently answer "no".
HOOK_FLAG_ABSENT_SENTINEL = "(no test-file edits flagged this iteration)"


# ----------------------------------------------------------------------
# Briefing data structures
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class TestStepBriefingInputs:
    """Closed-list inputs for test-step reviewer prompt construction.

    Per §F.impl.6 Input sources table — there are NO additional inputs
    accepted by `build_briefing(...)`; each field maps to a CLI-derived
    artifact.
    """

    slug: str
    iteration_n: int
    test_output: str
    iteration_diff: str
    hook_flag_content: str  # raw JSONL or HOOK_FLAG_ABSENT_SENTINEL
    prior_diagnosis: dict[str, Any] | None  # structured Sonnet output from prior iter
    iteration_metadata: dict[str, Any]  # driver-populated _metadata block


@dataclass(frozen=True)
class BoundaryBriefingInputs:
    """Closed-list inputs for phase-boundary reviewer prompt construction.

    Per plan §F.9.2 — the briefing builder reads structured artifacts
    from the slug directory and emits a deterministic briefing dict.
    """

    slug: str
    boundary: str  # "plan_to_implplan" | "implplan_to_code"
    plan_summary: dict[str, Any]
    orchestrator_shape: dict[str, Any]
    planner_telemetry: list[dict[str, Any]]


# ----------------------------------------------------------------------
# Public entry: build_briefing
# ----------------------------------------------------------------------

def build_briefing(
    *,
    slug: str,
    iteration_n: int,
    rubric_kind: rubric_mod.RubricKind = "test_step",
    plan_dir: Path,
    prior_diagnosis: dict[str, Any] | None = None,
    iteration_metadata: dict[str, Any] | None = None,
    debug_echo: bool = False,
) -> str:
    """Deterministically construct the runtime reviewer's prompt.

    **Anchor §4a.3 element 3 contract.** This function's parameter list
    is the closed set of CLI-derived inputs; there is NO parameter for
    agent-authored narrative, Opus session transcript, or coder prose.

    Parameters
    ----------
    slug : str
        Plan slug — resolves to ``docs/plans/<slug>/`` under repo root.
    iteration_n : int
        1-based iteration counter (test-step) or 1-based reviewer
        re-spawn counter (phase-boundary).
    rubric_kind : RubricKind
        "test_step" | "plan_to_implplan" | "implplan_to_code".
    plan_dir : Path
        Resolved slug directory; passed in so the CLI can override for
        tests.
    prior_diagnosis : dict | None
        Structured Sonnet output from the PRIOR iteration (test-step
        only). Per §F.impl.6: structured rubric output is permitted —
        it is NOT agent prose, it IS schema-validated structured data.
    iteration_metadata : dict | None
        Driver-populated `_metadata` block per §F.5 / §F.impl.5 — fed
        to Sonnet as input and echoed back in output. None defaults to
        a minimal stub computed from on-disk artifacts.
    debug_echo : bool
        When True, also writes the prompt to
        ``_sonnet_prompt_iter<N>.txt`` for retro-investigation per
        §F.impl.12 #1 ratified env-gated debug mode.

    Returns
    -------
    str
        The fully-constructed prompt body (system + user concatenation).
        Caller passes this verbatim to the SDK
        (`messages.create(..., messages=[{"role":"user","content": <body>}, ...]`).
    """
    if rubric_kind == "test_step":
        inputs = _gather_test_step_inputs(
            plan_dir=plan_dir,
            iteration_n=iteration_n,
            slug=slug,
            prior_diagnosis=prior_diagnosis,
            iteration_metadata=iteration_metadata,
        )
        prompt = _render_test_step_prompt(inputs)
    else:
        boundary_inputs = _gather_boundary_inputs(
            plan_dir=plan_dir,
            slug=slug,
            boundary=rubric_kind,
        )
        prompt = _render_boundary_prompt(boundary_inputs)

    if debug_echo:
        debug_path = plan_dir / DEBUG_PROMPT_FILENAME_TEMPLATE.format(n=iteration_n)
        try:
            debug_path.write_text(prompt, encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "debug-echo write failed for %s: %s", debug_path, exc,
            )
    return prompt


# ----------------------------------------------------------------------
# Coder briefing — companion to build_briefing for the Opus coder path
# ----------------------------------------------------------------------

def build_coder_briefing(
    *,
    slug: str,
    plan_dir: Path,
    iteration_n: int,
    prior_diagnosis: dict[str, Any] | None = None,
    chain_id: str | None = None,
) -> str:
    """Construct the per-iteration coder prompt for the Opus subagent.

    Companion to ``build_briefing(...)`` (which produces the reviewer
    prompt). Same closed-list discipline: ONLY CLI-derived inputs. The
    coder reads the orchestrator JSON itself for the task contract; the
    briefing here is the dispatch envelope — what slug, what iteration,
    what failed last time, what the prior reviewer said to fix.

    Parameters
    ----------
    slug : str
        Plan slug; resolves to ``docs/plans/<slug>/``.
    plan_dir : Path
        Resolved slug directory. Caller's responsibility to construct
        consistently with ``slug``.
    iteration_n : int
        1-based iteration counter. Iteration 1 has no prior test output
        or prior diagnosis (those slots render as explicit sentinels).
    prior_diagnosis : dict | None
        Structured rubric from the prior iteration's reviewer (R1-R5
        shape). Iteration 1 is None.
    chain_id : str | None
        Chain-overnight chain id for provenance. None when the coder
        is invoked outside chain-overnight (e.g., direct ``/code``).

    Returns
    -------
    str
        The user-side prompt body. The .claude/agents/coder.md system
        prompt is loaded separately by the SDK spawner via
        ``ClaudeAgentOptions.agents``.
    """
    parts: list[str] = []

    parts.append(f"# Coder dispatch — slug `{slug}`, iteration {iteration_n}")
    parts.append("")
    if chain_id is not None:
        parts.append(f"Chain id: `{chain_id}`")
        parts.append("")

    parts.append("## Task contract")
    parts.append("")
    parts.append(
        f"Read the orchestrator JSON at "
        f"`docs/plans/{slug}/{slug}_orchestrator.json` for the full "
        f"task graph (per-task `file_paths_touched`, `tests_enabled`, "
        f"`test_plan`, and dependency DAG). The chain driver has "
        f"already validated that the current task's `depends_on` are "
        f"satisfied; your job is to ship the task's `tests_enabled` "
        f"set green within its declared `file_paths_touched` scope."
    )
    parts.append("")

    parts.append("## Iteration state")
    parts.append("")
    if iteration_n == 1:
        parts.append(
            "This is iteration 1. No prior test output or reviewer "
            "diagnosis exists yet — implement the task from scratch."
        )
    else:
        parts.append(
            f"This is iteration {iteration_n}. Prior iterations failed "
            f"verification; the chain driver re-spawned you with the "
            f"reviewer's diagnosis embedded below."
        )
    parts.append("")

    if iteration_n > 1:
        prior_test_output_path = (
            plan_dir / TEST_OUTPUT_FILENAME_TEMPLATE.format(n=iteration_n - 1)
        )
        if prior_test_output_path.exists():
            try:
                prior_test_output = prior_test_output_path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError as exc:
                prior_test_output = (
                    f"(prior test output exists at {prior_test_output_path} "
                    f"but could not be read: {exc})"
                )
            parts.append(f"## Prior iteration ({iteration_n - 1}) test output")
            parts.append("")
            parts.append("```")
            parts.append(prior_test_output)
            parts.append("```")
            parts.append("")
        else:
            parts.append(f"## Prior iteration ({iteration_n - 1}) test output")
            parts.append("")
            parts.append(
                "(prior iteration's test output file not found; this is "
                "unusual but not fatal — proceed using the prior "
                "diagnosis below as your primary signal)"
            )
            parts.append("")

    if prior_diagnosis is not None:
        parts.append("## Prior reviewer diagnosis")
        parts.append("")
        parts.append(
            "Structured rubric from the prior iteration's reviewer "
            "(R1-R5 shape). R3 is the narrative diagnosis; R1/R2/R5 "
            "carry severity and remediation hints; R4 is the "
            "tampering-check verdict — if it's `yes-flagged`, the "
            "chain driver will have halted before re-spawning you, "
            "so seeing this here means R4 was `no` or `unclear`."
        )
        parts.append("")
        parts.append("```json")
        parts.append(json.dumps(prior_diagnosis, indent=2, sort_keys=True))
        parts.append("```")
        parts.append("")

    parts.append("## Your task")
    parts.append("")
    parts.append(
        "Edit ONLY the files in the current task's `file_paths_touched` "
        "set (the §G sealed-paths hook will reject writes outside it). "
        "Ship the task's `tests_enabled` green. The chain driver will "
        "run `pytest <tests_enabled>` after you return and either pass "
        "the loop (clean) or re-spawn you with a new reviewer diagnosis."
    )

    return "\n".join(parts) + "\n"


# ----------------------------------------------------------------------
# Test-step rendering — fixed concatenation order per §F.impl.6
# ----------------------------------------------------------------------

def _gather_test_step_inputs(
    *,
    plan_dir: Path,
    iteration_n: int,
    slug: str,
    prior_diagnosis: dict[str, Any] | None,
    iteration_metadata: dict[str, Any] | None,
) -> TestStepBriefingInputs:
    """Read the closed list of test-step inputs from the slug directory."""
    test_output_path = plan_dir / TEST_OUTPUT_FILENAME_TEMPLATE.format(n=iteration_n)
    if test_output_path.exists():
        test_output = test_output_path.read_text(encoding="utf-8", errors="replace")
    else:
        test_output = "(test output file not yet captured)"

    iteration_diff = _compute_iteration_diff(plan_dir)

    hook_flag_path = plan_dir / HOOK_FLAG_FILENAME_TEMPLATE.format(n=iteration_n)
    if hook_flag_path.exists():
        hook_flag_content = hook_flag_path.read_text(
            encoding="utf-8", errors="replace"
        ).strip() or HOOK_FLAG_ABSENT_SENTINEL
    else:
        hook_flag_content = HOOK_FLAG_ABSENT_SENTINEL

    if iteration_metadata is None:
        iteration_metadata = _default_metadata(
            test_output=test_output,
            iteration_diff=iteration_diff,
            hook_flag_content=hook_flag_content,
        )

    return TestStepBriefingInputs(
        slug=slug,
        iteration_n=iteration_n,
        test_output=test_output,
        iteration_diff=iteration_diff,
        hook_flag_content=hook_flag_content,
        prior_diagnosis=prior_diagnosis,
        iteration_metadata=iteration_metadata,
    )


def _render_test_step_prompt(inputs: TestStepBriefingInputs) -> str:
    """Fixed concatenation per §F.impl.6: system → user assembly.

    Order (verbatim per spec):
    1. System prompt — rubric verbatim.
    2. User prompt:
       a. ``## Iteration metadata`` JSON fence
       b. ``## Test runner output`` verbatim
       c. ``## Iteration diff`` verbatim
       d. ``## Test-file edit flag`` hook flag verbatim OR sentinel
       e. ``## Prior diagnosis`` JSON fence if iter > 1
    3. Schema enforcement via SDK ``output_config.format`` (caller-side).
    """
    parts: list[str] = []
    parts.append(_SYSTEM_PROMPT_TEST_STEP)
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append("## Iteration metadata")
    parts.append("")
    parts.append("```json")
    parts.append(json.dumps(inputs.iteration_metadata, indent=2, sort_keys=True))
    parts.append("```")
    parts.append("")
    parts.append("## Test runner output")
    parts.append("")
    parts.append("```")
    parts.append(inputs.test_output)
    parts.append("```")
    parts.append("")
    parts.append("## Iteration diff")
    parts.append("")
    parts.append("```diff")
    parts.append(inputs.iteration_diff)
    parts.append("```")
    parts.append("")
    parts.append("## Test-file edit flag")
    parts.append("")
    parts.append("```")
    parts.append(inputs.hook_flag_content)
    parts.append("```")
    if inputs.prior_diagnosis is not None and inputs.iteration_n > 1:
        parts.append("")
        parts.append("## Prior diagnosis")
        parts.append("")
        parts.append("```json")
        parts.append(json.dumps(inputs.prior_diagnosis, indent=2, sort_keys=True))
        parts.append("```")
    return "\n".join(parts)


_SYSTEM_PROMPT_TEST_STEP = """\
# Reviewer subagent — test-step retry iteration

You are reviewing an Opus coder iteration that attempted to fix failing
tests. Emit a constrained-rubric verdict in the JSON schema your
output_config enforces.

## R1 Root cause hypothesis
What is the most likely root cause of the remaining test failures?
[free-text, 1-3 sentences]

## R2 What did the fix miss?
[free-text describing the gap between the diff and the failing tests]

## R3 Concrete next-attempt action
What specific change should the next iteration make?
[free-text describing a concrete, narrowly-scoped action]

## R4 Tampering check (LOAD-BEARING)
Did this iteration edit test files in a way that weakens assertions?

- "no": iteration did not edit test files OR edits added assertions
  (strengthen)
- "yes-flagged": iteration removed assertions, broadened acceptable
  inputs, added skips/xfails/sys.exits, OR otherwise weakened assertion
  strength
- "unclear": evidence is ambiguous; recommend operator review

R4 == "yes-flagged" halts the retry loop regardless of test runner exit
code. Use the `## Test-file edit flag` section as the primary input.

## R5 Confidence in R3
- "high": fix likely succeeds in 1 iteration
- "medium": fix may succeed; alternative approach also plausible
- "low": uncertain; operator review may be needed

## _metadata
Driver-populated INPUT (echoed back in your output verbatim):
test_files_edited_this_iteration, test_runner_exit_code,
iteration_diff_lines_added, iteration_diff_lines_removed.

You do NOT have access to the Opus agent's narrative or conversation;
your evidence is the deterministic CLI artifacts below.
"""


# ----------------------------------------------------------------------
# Phase-boundary rendering — per §F.9.2
# ----------------------------------------------------------------------

def _gather_boundary_inputs(
    *,
    plan_dir: Path,
    slug: str,
    boundary: str,
) -> BoundaryBriefingInputs:
    """Read structured artifacts from the slug directory.

    Per plan §F.9.2: plan summary from ``<slug>_plan.json`` (§B
    substrate); per-task shape from ``<slug>_orchestrator.json``;
    planner telemetry from ``_orchestrator_log.jsonl`` (§C substrate).
    """
    plan_summary = _read_plan_summary(plan_dir, slug)
    orchestrator_shape = _read_orchestrator_shape(plan_dir, slug)
    planner_telemetry = _read_planner_telemetry(plan_dir)
    return BoundaryBriefingInputs(
        slug=slug,
        boundary=boundary,
        plan_summary=plan_summary,
        orchestrator_shape=orchestrator_shape,
        planner_telemetry=planner_telemetry,
    )


def _render_boundary_prompt(inputs: BoundaryBriefingInputs) -> str:
    """Render the phase-boundary briefing as a fully-formed prompt.

    The briefing is the WHOLE PROMPT — system prompt embeds the rubric;
    user prompt embeds the structured inputs as JSON fences. No
    agent-authored narrative anywhere.
    """
    parts: list[str] = []
    if inputs.boundary == "plan_to_implplan":
        parts.append(_SYSTEM_PROMPT_BOUNDARY_PLAN_TO_IMPLPLAN)
    elif inputs.boundary == "implplan_to_code":
        parts.append(_SYSTEM_PROMPT_BOUNDARY_IMPLPLAN_TO_CODE)
    else:
        raise ValueError(
            f"unknown boundary: {inputs.boundary!r} "
            f"(supported: plan_to_implplan, implplan_to_code)"
        )
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append("## Plan summary (from <slug>_plan.json)")
    parts.append("")
    parts.append("```json")
    parts.append(json.dumps(inputs.plan_summary, indent=2, sort_keys=True))
    parts.append("```")
    parts.append("")
    parts.append("## Orchestrator task shape (from <slug>_orchestrator.json)")
    parts.append("")
    parts.append("```json")
    parts.append(json.dumps(inputs.orchestrator_shape, indent=2, sort_keys=True))
    parts.append("```")
    parts.append("")
    parts.append("## Planner telemetry (from _orchestrator_log.jsonl)")
    parts.append("")
    parts.append("```json")
    parts.append(json.dumps(inputs.planner_telemetry, indent=2, sort_keys=True))
    parts.append("```")
    return "\n".join(parts)


_SYSTEM_PROMPT_BOUNDARY_PLAN_TO_IMPLPLAN = """\
# Reviewer subagent — plan → implplan boundary gate

You are reviewing whether the just-emitted plan substrate is coherent
enough for the implplan step to consume safely. Emit a constrained-
rubric verdict in the JSON schema your output_config enforces.

This is a RUNTIME boundary review (per splock plan §F.9.1 +
§F.9.2). You evaluate the CLI-derived artifacts below, NOT any
agent-authored summary.

## R1 Recon coverage
Does the plan's task list cover every WHY/WHAT element introduced in
recon?
- "complete" / "partial" / "gaps_identified"
- If not "complete": populate `R1_unaccounted_items`.

## R2 Deferred-now-or-do-now defensibility
Is each task's deferred-now-or-do-now status defensible? Are any "do
now" tasks lacking obvious entry criteria?
- "defensible" / "flag"
- If "flag": populate `R2_suspect_entries`.

## R3 Structural ambiguities
Are there structural ambiguities a downstream implplan step would have
to invent answers for?
- "none_found" / "flag"
- If "flag": populate `R3_ambiguity_list`.

## terminal_shape (LOAD-BEARING)
- "READY" — boundary cleared; chain proceeds to implplan.
- "NEEDS_REVISION" — chain re-spawns the plan step with your
  structured findings appended.
- "HALT" — problem the plan step cannot fix without operator
  judgment; chain halts via exit 10.

Your `terminal_shape` decision drives the chain driver's next move.
"""


_SYSTEM_PROMPT_BOUNDARY_IMPLPLAN_TO_CODE = """\
# Reviewer subagent — implplan → code boundary gate

You are reviewing whether the just-emitted orchestrator-plan substrate
is consistent enough for the code step to consume safely. Emit a
constrained-rubric verdict in the JSON schema your output_config
enforces.

This is a RUNTIME boundary review (per splock plan §F.9.1 +
§F.9.2). You evaluate the CLI-derived artifacts below, NOT any
agent-authored summary.

## R1 tests_enabled consistency
Does every task in `_state.json` have a `tests_enabled` entry
consistent with the plan's overall test discipline?
- "consistent" / "mismatch"
- If "mismatch": populate `R1_mismatched_task_ids`.

## R2 Concrete file paths and call sites
Are file paths and call sites concrete (no "TBD"-shaped placeholders
that would force the coder to re-plan)?
- "concrete" / "flag"
- If "flag": populate `R2_placeholder_sites`.

## R3 Dependency graph topology
Does the dependency graph (per `_state.json` `depends_on`)
topologically sort cleanly, or are there cycles?
- "dag" / "cycle_detected"
- If "cycle_detected": populate `R3_cycle_members`.

## R4 Sealed-paths references
Are sealed-state paths and credential paths referenced anywhere they
shouldn't be (per §G sealed-paths inventory + §M slopsquatting)?
- "clean" / "flag"
- If "flag": populate `R4_suspect_references`.

## terminal_shape (LOAD-BEARING)
- "READY" / "NEEDS_REVISION" / "HALT" — same semantics as plan →
  implplan boundary review.
"""


# ----------------------------------------------------------------------
# Helpers — CLI artifact reading (all CLI-derived; no agent prose)
# ----------------------------------------------------------------------

def _compute_iteration_diff(plan_dir: Path) -> str:
    """Compute ``git diff HEAD~1..HEAD`` at briefing-construction time.

    Per §F.impl.6 input sources table: iteration diff is captured via
    `git diff`, NOT via the Opus agent's description of changes. This is
    the structural exclusion mechanism for anchor §4a.3 element 3.

    Falls back to a sentinel when:
    - Not a git repo (e.g., test environment)
    - HEAD~1 does not exist
    - `git` not on PATH
    """
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD~1..HEAD"],
            cwd=str(plan_dir.parent if not (plan_dir / ".git").exists() else plan_dir),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("git diff capture failed: %s", exc)
        return "(git diff unavailable in this environment)"
    if result.returncode != 0:
        return "(git diff returned non-zero; no diff captured)"
    return result.stdout or "(empty diff)"


def _default_metadata(
    *,
    test_output: str,
    iteration_diff: str,
    hook_flag_content: str,
) -> dict[str, Any]:
    """Build a minimal `_metadata` block from CLI artifacts.

    Per §F.5 / §F.impl.5: driver populates `_metadata` from deterministic
    inputs BEFORE the reviewer spawn. The block is surfaced as INPUT and
    echoed back in OUTPUT.
    """
    added, removed = _count_diff_lines(iteration_diff)
    return {
        "test_files_edited_this_iteration": _extract_test_paths(hook_flag_content),
        "test_runner_exit_code": _guess_exit_code(test_output),
        "iteration_diff_lines_added": added,
        "iteration_diff_lines_removed": removed,
    }


def _count_diff_lines(diff: str) -> tuple[int, int]:
    """Count added / removed lines in a unified-diff string.

    Counts only ``+<text>`` and ``-<text>`` lines that are NOT file
    headers (``+++ / ---``). Returns (added, removed).
    """
    added = 0
    removed = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def _extract_test_paths(hook_flag_content: str) -> list[str]:
    """Extract test file paths from the JSONL hook-flag content.

    Each line is a JSON object with at least a ``path`` field. Missing /
    malformed lines are skipped silently — the hook is detection-only,
    not source of truth for path correctness.
    """
    if not hook_flag_content or hook_flag_content == HOOK_FLAG_ABSENT_SENTINEL:
        return []
    paths: list[str] = []
    for line in hook_flag_content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        path = entry.get("path") if isinstance(entry, dict) else None
        if isinstance(path, str) and path:
            paths.append(path)
    return paths


def _guess_exit_code(test_output: str) -> int:
    """Sentinel exit-code extraction from test runner output.

    The reviewer's `_metadata` requires an integer; the driver may
    populate this from `bin/verify`'s exit code directly when invoking
    the briefing builder. This helper is a fallback when the metadata
    wasn't pre-computed (e.g., test environments).
    """
    # Heuristic — look for typical pytest summary markers.
    if "passed" in test_output.lower() and "failed" not in test_output.lower():
        return 0
    return 1


def _read_plan_summary(plan_dir: Path, slug: str) -> dict[str, Any]:
    """Read ``<slug>_plan.json`` and return a summary dict.

    Returns empty dict if missing — the runtime gate caller surfaces
    "(no plan substrate found)" elsewhere. This module does NOT raise;
    the briefing is best-effort assembly.
    """
    plan_path = plan_dir / f"{slug}_plan.json"
    if not plan_path.exists():
        return {}
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Surface a slim summary; the reviewer doesn't need the whole plan body.
    return {
        "schema_version": data.get("schema_version"),
        "slug": data.get("slug"),
        "tier": data.get("tier"),
        "tasks_skeleton": data.get("tasks_skeleton", []),
        "produced_at": data.get("produced_at"),
    }


def _read_orchestrator_shape(plan_dir: Path, slug: str) -> dict[str, Any]:
    """Read ``<slug>_orchestrator.json`` and return a shape summary.

    Surfaces task count, depends_on graph, deferred ratio — the load-
    bearing inputs for the implplan → code rubric per plan §F.9.3.
    """
    orch_path = plan_dir / f"{slug}_orchestrator.json"
    if not orch_path.exists():
        return {}
    try:
        data = json.loads(orch_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    tasks = data.get("tasks", [])
    deferred = sum(1 for t in tasks if t.get("status") == "deferred")
    task_count = len(tasks) if isinstance(tasks, list) else 0
    depends_on_graph: list[dict[str, Any]] = []
    if isinstance(tasks, list):
        for t in tasks:
            if not isinstance(t, dict):
                continue
            depends_on_graph.append({
                "id": t.get("id"),
                "depends_on": t.get("depends_on", []),
                "tests_enabled": t.get("tests_enabled"),
            })
    return {
        "schema_version": data.get("schema_version"),
        "slug": data.get("slug"),
        "task_count": task_count,
        "deferred_count": deferred,
        "deferred_ratio": deferred / task_count if task_count else 0.0,
        "depends_on_graph": depends_on_graph,
    }


def _read_planner_telemetry(plan_dir: Path) -> list[dict[str, Any]]:
    """Read planner Call 1 / Call 2 rows from ``_orchestrator_log.jsonl``.

    Returns a list of rows with `event_type` in the planner family. Per
    plan §F.9.2: model used + Call 1 vs Call 2 token counts surface to
    the reviewer.
    """
    log_path = plan_dir / "_orchestrator_log.jsonl"
    if not log_path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            event_type = row.get("event_type", "") if isinstance(row, dict) else ""
            if not isinstance(event_type, str):
                continue
            if "planner" in event_type.lower():
                out.append({
                    "event_type": event_type,
                    "ts": row.get("ts"),
                    "emitted_by": row.get("emitted_by"),
                    "model_id": row.get("model_id"),
                    "input_tokens": row.get("input_tokens"),
                    "output_tokens": row.get("output_tokens"),
                })
    except OSError:
        return []
    return out


__all__ = [
    "BoundaryBriefingInputs",
    "DEBUG_PROMPT_FILENAME_TEMPLATE",
    "HOOK_FLAG_ABSENT_SENTINEL",
    "HOOK_FLAG_FILENAME_TEMPLATE",
    "TEST_OUTPUT_FILENAME_TEMPLATE",
    "TestStepBriefingInputs",
    "build_briefing",
]
