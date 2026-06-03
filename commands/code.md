---
description: Code one orchestrator task — spawn coder subagent against <slug>_orchestrator.json::tasks[<task-id>] (auto-picks next-ready task when task-id omitted; optional operator directive after `--`)
argument-hint: <slug> [<task-id>] [-- <directive>]
---

# /code — operator-direct coder entry (per-task)

Triggered by the operator with: `/code $ARGUMENTS`

Where `$ARGUMENTS` is a positional sequence with an optional `--`
sentinel separating fixed positional tokens (slug + optional task-id)
from a free-text operator directive:

- **One token** (`<slug>` only) — **auto-pick mode**. The skill shells to
  `./bin/orchestrator-next-ready <slug>` and uses its stdout as the
  task-id. An "Auto-picked T<n>: <task-title>" notice surfaces to the
  operator before the coder spawns.
- **Two tokens** (`<slug> <task-id>`) — **explicit mode**. The operator
  picks the task by id. `<task-id>` MUST match the regex
  `^T[A-Za-z0-9_-]+$` (the loosened task-id pattern shared with the
  orchestrator-log schema at
  `schemas/orchestrator_log_v1.schema.json:27-32` — leading `T`,
  alphanumeric/underscore/hyphen tail).
- **Slug + `--` + directive** (`<slug> -- <directive prose>`) —
  **auto-pick mode + operator directive**. The picker chooses the
  task-id; everything after `--` is passed to the coder spawn prompt
  as a wrapped `<operator-directive>` block.
- **Slug + task-id + `--` + directive** (`<slug> <task-id> -- <directive prose>`)
  — **explicit mode + operator directive**.
- **Auto-pick mode without `--`** is also accepted when position-2 is
  non-empty but does NOT match `^T[A-Za-z0-9_-]+$`: the entire tail
  from position-2 onward is treated as the directive (auto-pick mode +
  directive). This is the "natural prose" affordance — an operator
  typing `/code my_slug focus on the gate logic` gets the same effect
  as `/code my_slug -- focus on the gate logic`. The `--` sentinel is
  the unambiguous form; the regex-fallback is for operator
  convenience.

This command spawns the `coder` subagent (per `.claude/agents/coder.md`)
to execute task entries from `<slug>_orchestrator.json` under the
§A Ralph completion gate, **looping across the DAG** until completion
or a fundamental blocker (per step 6 below).

## /code does not accept `--reopen`

`/code` produces NO standalone MD artifact (the output IS the code
changes plus orchestrator-log rows appended via `bin/update_orchestrator`).
The `--reopen` flag — which the gated commands (`/qa`, `/plan`,
`/implplan`) use to bypass a target-file-exists refusal — has no
meaning for `/code` because there is no target file to overwrite.

**`--reopen` does not apply to /code.** If an operator wants to re-run a
task that has already been marked `done`, the equivalent operator gesture
is to flip the task's state back to `ready` via the orchestrator-state
CLI, then re-invoke `/code`:

```bash
bin/update_orchestrator <slug> <task-id> ready
/code <slug> <task-id>
```

This split is deliberate. `bin/update_orchestrator` carries the
seven-status state machine + log-emission contract (per plan §C.1) — the
slash command does not duplicate that surface.

## File-existence + structural gate

Per v2.7 §1.C, REFUSE if:

- `docs/plans/<slug>/<slug>_orchestrator.json` does NOT exist (run
  `/plan` then `/implplan` first).
- The given `<task-id>` is NOT present in the orchestrator's `tasks`
  array (the operator picked an invalid task id, or auto-pick produced
  an id that has since been removed from the orchestrator).

Check both via Bash + `jq` before spawning. Example check:

```bash
jq -e --arg tid "<task-id>" '.tasks[] | select(.id == $tid)' \
    docs/plans/<slug>/<slug>_orchestrator.json
```

`jq -e` exits non-zero if no task matches.

**Schema field name is `id`, not `task_id`** — per
`schemas/orchestrator_v1.schema.json:42`. The legacy jq filter selected
on the wrong field name and returned zero matches against every task;
T3 ships the fix.

## What to do

1. **Parse `$ARGUMENTS` with the `--` sentinel + regex fallback.**

   Procedure:

   1. Find the first standalone `--` token in `$ARGUMENTS` (a token
      bounded by whitespace, not the prefix of a longer flag).
   2. If `--` is present:
      - The tokens to the LEFT of `--` are the positional segment.
      - The tokens to the RIGHT of `--` are the directive segment
        (preserve internal whitespace; trim leading/trailing).
      - REFUSE if there is no content after `--` (operator wrote the
        sentinel but no directive prose).
   3. If `--` is absent, the entire `$ARGUMENTS` is the positional
      segment and there is no directive.
   4. Parse the positional segment as 1 or 2+ whitespace-separated
      tokens:
      - **Zero positional tokens**: refuse with a usage error citing
        the front-matter argument-hint.
      - **One positional token** (`<slug>`): enter **auto-pick mode**
        (step 2a below). No task-id given.
      - **Two or more positional tokens** AND position-2 matches the
        regex `^T[A-Za-z0-9_-]+$`: position-1 is the slug, position-2
        is the explicit task-id, any further positional tokens are a
        usage error (no positional content is allowed after a valid
        task-id without the `--` sentinel).
      - **Two or more positional tokens** AND position-2 does NOT
        match the regex AND `--` was NOT present: this is the
        "natural prose" auto-pick + directive affordance — treat the
        entire tail (position-2 onward, joined with single spaces) as
        the directive segment, with no explicit task-id. Auto-pick
        applies.
   5. Validation summary — REFUSE with a clear usage error when:
      - Zero positional tokens.
      - `--` is present but the directive segment is empty.
      - Three or more positional tokens AND position-2 matches the
        task-id regex AND `--` is absent (e.g. `<slug> T3 some text`
        without the sentinel is ambiguous — surface the front-matter
        argument-hint and ask the operator to add `--`).

   Worked examples:

   | Input | Mode | task-id source | directive |
   |---|---|---|---|
   | `my_slug` | auto-pick | picker stdout | none |
   | `my_slug T3` | explicit | `T3` | none |
   | `my_slug -- focus on the gate logic` | auto-pick + directive | picker stdout | `focus on the gate logic` |
   | `my_slug T3 -- focus on the gate logic` | explicit + directive | `T3` | `focus on the gate logic` |
   | `my_slug focus on the gate logic` | auto-pick + directive | picker stdout | `focus on the gate logic` (regex-fallback, position-2 doesn't match `^T[A-Za-z0-9_-]+$`) |
   | (empty) | refuse | — | — |
   | `my_slug --` | refuse (empty directive) | — | — |
   | `my_slug T3 extra-token` | refuse (ambiguous; suggest `--`) | — | — |

2a. **Auto-pick mode.** Run:

```bash
./bin/orchestrator-next-ready <slug>
```

   - On `exit 0`: stdout contains the first-ready task-id (e.g., `T3`).
     Capture it as `<task-id>`. Also capture the task title from the
     orchestrator JSON via:

```bash
jq -r --arg tid "<task-id>" '.tasks[] | select(.id == $tid) | .title' \
    docs/plans/<slug>/<slug>_orchestrator.json
```

     Emit a one-line notice **before spawning the coder**:

```
Auto-picked T<n>: <task-title>
```

     Then proceed to step 3 with the auto-picked id.

   - On non-zero exit, refuse with the per-verdict phrase below
     (closed-enum per `bin/_orchestrator_query/exit_codes.py`):

     | Exit | Refusal phrase |
     |---|---|
     | 10 (SLUG_NOT_FOUND) | "plan dir not found — confirm slug name and that /implplan has been run" |
     | 11 / 12 (ORCHESTRATOR_JSON_MISSING / MALFORMED) | "orchestrator JSON unreadable — run /implplan to regenerate" |
     | 13 (STATE_SHAPE_INVALID) | "_state.json has neither dict nor array tasks — manual repair needed" |
     | 20 (ALL_BLOCKED) | "no ready task; all unfinished tasks are blocked — investigate dep chain" |
     | 21 (ALL_WIP) | "no ready task; some tasks are in flight — wait for completion or check stuck tasks" |
     | 22 (MIXED) | "no ready task but plan not done — review state for inconsistency" |
     | 23 (ALL_DONE) | "plan complete — all tasks done; run /test next, then close per conventions_closed_and_history.md §A (closeout + git mv to docs/plans/_closed/<slug>/)" |

     Print the exit code AND the phrase, then halt without spawning the
     coder.

2b. **Explicit mode.** Skip the picker call. Use the operator-supplied
    task-id directly.

3. **Run the file-existence + structural gate** (above). On refusal,
   print the failing condition and exit.

4. **Wrap the operator directive (if present)** using `bin/wrap`. This
   step runs ONLY when the parse identified a non-empty directive
   segment. Mirrors the pattern used by `/recon`, `/research`, `/qna`
   (per std_command_operator_extensions task TG2 / TF). The wrap step
   yields a canonical `<operator-directive>...</operator-directive>`
   block that is threaded into the coder spawn prompt:

```bash
# Conditional: only when directive segment is non-empty.
WRAPPED_DIRECTIVE="$(bin/wrap --kind operator-directive --content "$directive")"
```

   `bin/wrap` enforces the 8KB cap (per SC10) and refuses on closed-enum
   kind violations — propagate any non-zero exit verbatim and halt
   without spawning the coder.

5. **Spawn the `coder` subagent via the Agent tool:**
   - `subagent_type`: `coder`
   - `description`: "Code task <task-id> for <slug>"
   - `prompt`: tell the subagent
     - the slug + task-id (auto-picked or explicit; surface which mode
       you used)
     - the path to the orchestrator JSON
     - the §A Ralph completion gate contract — write code at
       `file_paths_touched`, run tests at `tests_enabled`, refuse to
       declare completion until the `verifier` subagent answers READY
     - state mutations to `_state.json` and `_orchestrator_log.jsonl`
       MUST go through `bin/update_orchestrator` (per plan §C.1)
     - the §G hook stack enforces path scoping (sealed-paths,
       suppression-block, chain-test-file-edit-flag) — not bypassable
     - **if `WRAPPED_DIRECTIVE` was produced in step 4**, append it to
       the prompt as-is. The wrap shape is already
       `<operator-directive>...</operator-directive>` — pass it through
       verbatim. The coder's frontmatter + body cite the v2.7 §D.3
       delimiter-discipline contract (per TI), so the subagent knows to
       treat the wrapped content as high-trust guidance with
       byte-level-data discipline.

6. **Auto-loop across the DAG until completion or a fundamental
   blocker.** After the coder returns READY for the just-completed task,
   **DO NOT halt and surface to the operator yet**. Instead:

   1. **Junction-halt check** — BEFORE re-running the picker. Read
      `docs/plans/<slug>/<slug>_orchestrator.json` and scan its
      `junctions[]` array for entries where `after_task` equals the
      just-completed task-id. The picker is unaware of junctions (per
      `bin/_orchestrator_query/orchestrator_loader.py:9` — "junctions
      ... opaque to the library"), and `/review` only covers the two
      meta-boundaries (`plan_to_implplan`, `implplan_to_code`) — not
      these intra-implplan junctions. So this halt MUST be enforced
      in the skill loop. For each matching junction, emit one notice
      keyed by `kind`:

      | kind | Notice template |
      |---|---|
      | `phase_boundary` | `Phase boundary <J-id> reached after <T-id> — commit/checkpoint Phase N work before re-invoking /code <slug>` |
      | `test_gate` | `Test gate <J-id> after <T-id> — run /test <slug> before re-invoking /code <slug>` |
      | `review_gate` | `Review gate <J-id> after <T-id> — operator review required before re-invoking /code <slug>` |

      Multiple junctions can fire on the same `after_task` (e.g., a
      task that ends a phase AND triggers a walkthrough test — emit
      all matching notices, in JSON-declared order). If ANY junction
      matched, halt the loop and proceed to the terminal summary in
      `## Output`; do NOT call the picker.

   2. Re-run `./bin/orchestrator-next-ready <slug>`.
   3. **Exit 0** — capture the new task-id, emit a fresh
      `Auto-picked T<n>: <task-title>` notice, then **jump back to
      step 3 of the outer "What to do" sequence** (file-existence
      gate → step 5 spawn — NOT step 3 of this inner numbered list).
      The operator directive from the originating invocation is NOT
      carried forward across loop iterations; iterations beyond the
      first run with no directive unless the operator re-invokes
      `/code`.
   4. **Exit 23 (ALL_DONE)** — the DAG is closed. Proceed to the
      terminal summary in `## Output` below.
   5. **Exit 20/21/22 (ALL_BLOCKED / ALL_WIP / MIXED)** — surface the
      matching phrase from step 2a's table as part of the terminal
      summary. These are operator-visible halt conditions but are not
      themselves "fundamental blockers" — they describe DAG state.

   **DO NOT pause between tasks for "advance to T<N+1>?" prompts.**
   The loop is the default per the
   `feedback-framework-traverse-autonomously` memory. Mid-chain
   operator nudging is explicitly out of contract — v2.7 §1.C's design
   intent is that gates fire at meaningful decision points (junctions,
   phase boundaries, verifier verdicts), not at every task boundary.

   **Auto-handled in-loop (NO operator pause):**

   - Verifier `NEEDS_HUMAN` with a narrow mechanical fix (typo, wrong
     literal, env-var semantics, missing import) → dispatch a targeted
     fixup coder pass on the same task-id, re-verify, then continue.
   - Verifier `NO` with a clear deterministic remediation (test
     isolation bug, refactor regression) → debug + fix in-line on the
     same task-id, re-verify, then continue.
   - Cross-cutting issue flagged by a coder that the framework
     correctly assigns to a separate follow-up task → continue the
     chain; capture the follow-up note for the terminal summary.
   - Tooling-level mechanical fixes that unblock the chain (e.g.,
     schema-version drift, low-risk one-line bug) → fix forward,
     document briefly, continue.

   **Fundamental blockers that DO halt the loop for HITL:**

   - Verifier returns `NO` **twice on the same task** with no clear
     deterministic fix (suggests deeper design mismatch — operator
     needs to weigh in on direction).
   - Architectural decision branches with non-trivial tradeoffs (e.g.,
     "rewrite `run_iteration` vs adapter shim") where operator
     preference is load-bearing.
   - Attempted writes to sealed paths gated to the operator
     (`.claude/{agents,hooks,commands}/**`) — stage to `/tmp` and
     surface for an explicit `cp`.
   - Destructive operations on shared state (force-push, schema drop,
     revert of work that may represent operator's in-progress
     changes).
   - Discovered upstream contract gap requiring cross-repo or
     cross-team coordination (e.g. an upstream/downstream repo or
     another team's in-flight work).

   On a fundamental blocker, halt the loop and proceed to the terminal
   summary, including the specific blocker description and the task-id
   it surfaced on.

## Output

The coder writes code directly via Edit/Write (its tools include the
full code-writer surface — per `.claude/agents/coder.md`). There is no
MD artifact produced by `/code`; the output IS the code changes plus
any orchestrator-log rows appended via `bin/update_orchestrator`.

**One terminal summary, at the end of the loop.** After the auto-loop
in step 6 halts (ALL_DONE, BLOCKED/WIP/MIXED, or a fundamental
blocker), surface ONE consolidated message to the operator:

- Tasks completed this session (one bullet each: `T-id — title`).
- Any deferred follow-ups captured mid-chain (with the originating
  task-id and a short description).
- The halt condition:
  - `ALL_DONE` — emit `plan complete — all tasks done`, then surface the close per
    `docs/plans/splock/conventions_closed_and_history.md` §A: once the
    work is committed/merged and `_state.json` is terminal, author
    `<slug>_closeout.md` (§C 9-section template) and `git mv docs/plans/<slug>/
    docs/plans/_closed/<slug>/` (whole subtree, no date suffix); run `/test
    <slug>` first if it has not been run. Leaving a terminal slug in the active
    dir without a `scheduled_markers/list.md` hold-back is drift (the
    `substrate_drift_audit` batch cleans it up).
  - `ALL_BLOCKED / ALL_WIP / MIXED` — emit the matching phrase from
    step 2a's table.
  - Junction halt — emit the per-kind notice(s) from step 6.1, naming
    the junction id, its kind, the `after_task` it fired on, and the
    operator gesture to resume (`commit/checkpoint`, `/test <slug>`,
    or `operator review`).
  - Fundamental blocker — emit the specific blocker category and the
    task-id where it surfaced, plus the operator action required.

**Do NOT emit per-task summaries mid-loop.** The coder's per-task
output (pass/fail, files touched, verifier verdict) is captured in
the orchestrator log + state files; surface only the terminal
summary so the operator gets one clean handoff at chain end.

## Important — coder vs `/test`

`/code` does NOT itself run the test-step retry loop with reviewer
iterations. That's `/test <slug>` (which invokes `bin/verify test-step`
and includes the Sonnet R1-R5 reviewer loop). The coder runs tests
inline as part of its own work, but the structured retry-and-review
machinery is `/test`'s job.

## Cross-references

- `.claude/agents/coder.md` — subagent contract + tools + hook stack
- `bin/orchestrator-next-ready` — auto-pick CLI (POSIX wrapper around
  `python -m bin._orchestrator_query.main`)
- `bin/_orchestrator_query/exit_codes.py` — closed-enum exit code source
  of truth (codes 0/2/10/11/12/13/20/21/22/23)
- `bin/wrap` — operator-directive wrap helper (POSIX wrapper around
  `python -m bin._wrap.main`); closed `--kind` enum sourced from
  `bin._planner.external_input_sanitize.WrapKind`; 8KB cap per SC10
- `schemas/orchestrator_log_v1.schema.json:27-32` — `TaskId` pattern
  source-of-truth (`^T[A-Za-z0-9_-]+$`)
- `bin/update_orchestrator` — state-mutation CLI; use this (not
  `--reopen`) to flip a `done` task back to `ready` for a re-run
- v2.7 §1.C — /code spec
- v2.7 §1.D + §6.G.4 — coder prompt-content + per-phase commit discipline
- plan §A — Ralph completion gate
- plan §D.8.5 — coder frontmatter + path-restriction discipline
- Hole H.19 — `tests_enabled` structural tightening
- `docs/plans/code_next_ready_pick/code_next_ready_pick_plan.md` — T0
  evidence trail + branch decisions for the auto-pick wiring
- `docs/plans/std_command_operator_extensions/` — task TH (this file's
  `--` sentinel + directive plumbing) + TF (`bin/wrap` substrate)
- `feedback-framework-traverse-autonomously` memory — source-of-truth
  for the step-6 auto-loop contract + fundamental-blocker enumeration
