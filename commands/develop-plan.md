---
description: Autonomously develop a multi-phase plan doc by spawning implementer + evaluator agents per phase with no human gates between phases
argument-hint: <plan-path-relative-to-repo-root>
---

# /develop-plan — autonomous multi-phase plan executor

Triggered by the user with: `/develop-plan $ARGUMENTS`

Where `$ARGUMENTS` is a path to a plan markdown file (e.g.
`docs/plans/investigator_tier_catalog.md`).

You are now the **orchestrator** for that plan. Your job: drive every
phase to completion through implementer + evaluator agents, maintain
the plan's `Status` column + `Orchestrator log`, and surface to the
human only when persistently blocked.

---

## Required plan structure

The target plan must contain:

1. A **Phasing** section with a markdown table whose first columns are
   `| Phase | Status | ... | Deliverables |`. The `Status` column is
   the orchestrator's state field per phase.
2. An **Orchestrator log** section near the bottom of the doc with an
   `### Entries` heading. You append log entries below the
   `<!-- Orchestrator appends below this line. Do not edit by hand. -->`
   marker.
3. (Recommended) A **Test plan** section listing acceptance criteria
   per phase. The evaluator references this.

If the doc is missing the `Status` column or `Orchestrator log`,
**add them first** before starting orchestration:

- Status column initialized to `not_started` for every existing phase row.
- Orchestrator log section appended at the doc tail with the
  `<!-- Orchestrator appends below this line. -->` marker.

---

## Status enum

The `Status` column transitions through these values (and only these):

| Status | Meaning |
|---|---|
| `not_started` | Phase has never been worked. |
| `in_progress` | Implementer agent is running. |
| `awaiting_eval` | Implementer returned. Evaluator about to spawn or running. |
| `revisions_requested:R<n>` | Evaluator returned CHANGES_REQUESTED at round n; next implementer spawn will incorporate findings. |
| `completed` | Evaluator returned APPROVE. Phase locked. |
| `blocked` | Hit the revision cap (default 5). Surfaced to human. |

Only the orchestrator (you) writes the Status column. Implementer and
evaluator agents must not modify it.

---

## Per-phase loop

Repeat the following until all phases are `completed` or one is
`blocked`:

### Step 0 — Pick the next phase

1. Read the plan's Phasing table.
2. Find the first row whose Status is not `completed` and not
   `blocked`. That's the active phase.
3. If every phase is `completed`: orchestration done — append a final
   log entry "ALL PHASES COMPLETE", report to the human, stop.
4. If a row is `blocked`: orchestration halted — report to human, stop.

### Step 1 — Spawn implementer (background)

1. Update Status: `not_started`/`revisions_requested:R<n>` → `in_progress`.
   Edit the plan doc's Phasing table to set the new status.
2. Append a log entry to the Orchestrator log.
3. Read the plan's Test plan section to extract acceptance criteria
   for this phase (you'll pass them to both agents).
4. Spawn an Agent with `subagent_type='general-purpose'` and
   `run_in_background=true`. The prompt is the **Implementer prompt
   template** below — fill in the template with phase-specific
   deliverables and acceptance criteria from the plan.
5. End your turn (do not poll). The system will deliver the
   implementer's completion as a tool result that wakes you up.

### Step 2 — Implementer returns

When the background implementer's tool result arrives:

1. Update Status: `in_progress` → `awaiting_eval`.
2. Append a log entry summarizing what the implementer reported.
3. Note any blockers the implementer surfaced. If the implementer
   reports a hard blocker that prevents completion (e.g. needs DDL
   that requires human gesture, ambiguous requirement, missing
   dependency): mark Status → `blocked`, log it, report to human, stop.
4. Otherwise proceed to Step 3.

### Step 3 — Spawn evaluator (foreground)

1. Spawn an Agent with `subagent_type='general-purpose'`, **foreground**
   (no `run_in_background`). Use the **Evaluator prompt template** below.
2. The evaluator reads the new files, runs the tests, checks
   acceptance criteria, and returns a structured verdict.
3. The evaluator must NOT edit code or the plan doc — review only.

### Step 4 — Branch on verdict

Parse the evaluator's response. Look for the line `VERDICT: APPROVE`
or `VERDICT: CHANGES_REQUESTED` (case-sensitive).

**If APPROVE:**

1. Update Status: `awaiting_eval` → `completed`. Add a date stamp
   (e.g. `completed (2026-05-10)`).
2. Append a log entry citing the evaluator's summary.
3. Loop back to Step 0 (next phase).

**If CHANGES_REQUESTED:**

1. Determine the current revision round. If the current Status is
   `revisions_requested:R<n>`, the next round is `n+1`. Otherwise round 1.
2. If the next round would exceed the cap (default 5):
   - Update Status: → `blocked`.
   - Append a log entry naming the cap and the persistent findings.
   - Report to the human with: "Phase N blocked after 5 revision rounds.
     Latest findings: ...". Stop.
3. Otherwise:
   - Update Status: `awaiting_eval` → `revisions_requested:R<next>`.
   - Append a log entry with the evaluator's findings (verbatim, in
     a fenced block).
   - Spawn a new implementer (Step 1) with the **revision prompt
     template** appended to the base implementer prompt — passing the
     evaluator's findings + the prior implementer's summary.

---

## Implementer prompt template

```
You are the Phase {N} implementer for plan: {plan_path}

Plan repo root: {repo_root}
Read the plan doc at the plan path before starting. Focus only on
Phase {N} — do not advance to other phases.

Phase {N} deliverables (from the plan's Phasing table):
{phase_deliverables}

Phase {N} acceptance criteria (from the plan's Test plan section):
{phase_acceptance_criteria}

Hard constraints — violation will be flagged by the evaluator and
counted as a revision round:

1. Do NOT edit the Phasing table or the Status column in the plan doc.
2. Do NOT edit the Orchestrator log section in the plan doc.
3. Do NOT modify any phase number other than {N}.
4. Do NOT apply DDL or run migrations. If the phase requires schema
   changes, return without applying and report the blocker.
5. Do NOT push, force-push, force-pull, or rewrite git history.
6. Do NOT commit unless the plan explicitly asks for a commit at this
   phase. The orchestrator manages commits separately.
7. When you write tests, RUN them. A deliverable that mentions a test
   is not complete until the test passes. Report test output verbatim
   in your summary.
8. Use the project venv when running anything Python:
   `source "$SPLOCK_VENV/bin/activate"`
9. Use module-style invocation: `python -m <package>.<module>`.

When you believe Phase {N} deliverables are complete:

Return a structured summary in this exact format:

DELIVERABLES_STATUS: COMPLETE
or
DELIVERABLES_STATUS: BLOCKED — <one-line reason>

FILES_CHANGED:
- <relative/path/to/file>: <brief description of change>
- ...

TESTS_RUN:
- <test path or command>: <PASS|FAIL|N/A> — <output snippet if FAIL>
- ...

NOTES:
<anything the evaluator should know — design choices, deviations from
plan, things to verify carefully, follow-ups out of scope>

The orchestrator will spawn an evaluator agent on your output. The
evaluator may request changes; if so, you'll be re-spawned with their
findings as additional context.
```

---

## Evaluator prompt template

```
You are the Phase {N} evaluator for plan: {plan_path}

Plan repo root: {repo_root}

Your job: independently verify that the Phase {N} implementer's work
satisfies the deliverables and acceptance criteria from the plan.
You are a fresh agent — do NOT trust the implementer's claims;
verify by reading the code and running the tests yourself.

Phase {N} deliverables (from the plan):
{phase_deliverables}

Phase {N} acceptance criteria (from the plan):
{phase_acceptance_criteria}

The implementer's self-report:
{implementer_summary}

Verification steps you MUST perform:

1. Read every file the implementer claims to have changed; verify the
   change matches the deliverable.
2. Run every test the implementer ran. Confirm PASS independently.
   Run with: `source "$SPLOCK_VENV/bin/activate"
   && python -m pytest <path> -xvs`
3. Check that the implementer respected hard constraints:
   - Phasing table not modified (run: `git diff <plan_path>` and look
     at the Phasing table region)
   - Status column not modified by implementer
   - Orchestrator log not modified by implementer
   - No DDL applied (look for migration apply runs in git log; check
     for new tables that weren't there pre-phase)
   - No git history rewrite (check `git reflog`)
4. For acceptance criteria that aren't tests, verify by reading
   relevant code or running ad-hoc commands. Document each check.

Hard constraints on you:

- Do NOT edit any code or the plan doc.
- Do NOT mark anything in the plan as completed.
- Run tests in read-only mode where possible; do not commit anything.

Return your verdict in this exact format. The orchestrator parses
the VERDICT line literally.

VERDICT: APPROVE
or
VERDICT: CHANGES_REQUESTED

DELIVERABLES_REVIEW:
- <deliverable>: <PASS|FAIL> — <one-line evidence>
- ...

TESTS_VERIFIED:
- <test path>: <PASS|FAIL> — <output snippet if FAIL>
- ...

CONSTRAINT_CHECKS:
- Phasing table unchanged: <yes|no — diff snippet if no>
- Status column unchanged by implementer: <yes|no>
- Orchestrator log unchanged by implementer: <yes|no>
- No DDL applied: <yes|no>
- No git history rewrite: <yes|no>

FINDINGS_FOR_REVISION:  (omit if VERDICT=APPROVE)
- <file:line — concrete issue and what should change>
- ...

SUMMARY:
<2-3 sentences: what was done well, what's still missing if anything>
```

---

## Revision prompt addendum

When re-spawning the implementer for a CHANGES_REQUESTED round,
append the following to the base implementer prompt template:

```
=== REVISION ROUND {next_round} ===

Your prior implementer attempt was reviewed by an evaluator and
flagged for revision. You're a fresh agent — read the prior summary
+ findings before continuing.

Prior implementer's last summary:
{prior_implementer_summary}

Evaluator's findings (address each):
{evaluator_findings_verbatim}

Address every item in FINDINGS_FOR_REVISION. The next evaluator
round will re-check the same constraints + re-run tests.
```

---

## Log entry format

Every status transition adds one line under the `### Entries`
heading, after the `<!-- Orchestrator appends below this line. -->`
marker. Use this exact format:

```
- YYYY-MM-DD HH:MM — phase=<N> status=<old>→<new> actor=<orchestrator|implementer|evaluator> [round=<R>] note=<short summary>
```

Examples:

```
- 2026-05-10 17:30 — phase=1 status=not_started→in_progress actor=orchestrator note=spawned implementer
- 2026-05-10 18:42 — phase=1 status=in_progress→awaiting_eval actor=implementer note=DELIVERABLES_STATUS=COMPLETE; 5 files changed, 2 test files PASS
- 2026-05-10 18:50 — phase=1 status=awaiting_eval→revisions_requested:R1 actor=evaluator note=parity test missing tier-3 fixture; 2 findings
- 2026-05-10 19:35 — phase=1 status=revisions_requested:R1→in_progress actor=orchestrator round=2 note=re-spawned implementer with findings
- 2026-05-10 20:10 — phase=1 status=in_progress→awaiting_eval actor=implementer note=fixture added; tests PASS
- 2026-05-10 20:18 — phase=1 status=awaiting_eval→completed actor=evaluator note=APPROVE; all deliverables verified
```

Use UTC or local time consistently across one orchestration run.
Don't backfill or rewrite past entries.

---

## Resumption / state recovery

If `/develop-plan` is re-invoked mid-orchestration (e.g. the user's
session was interrupted and they're picking up later):

1. Read the plan's Phasing table to determine current state per phase.
2. Read the Orchestrator log to find the last entry per phase.
3. Pick up at the appropriate step:
   - If active phase status is `in_progress`: ambiguous — the prior
     implementer's session is gone. Re-spawn implementer fresh; note
     in log that this is a resumption.
   - If `awaiting_eval`: spawn evaluator now.
   - If `revisions_requested:R<n>`: re-spawn implementer with the
     last evaluator findings from the log; treat as a continuation
     of round n.
   - If `completed` for every phase: orchestration done.

The plan doc + filesystem are the durable state; agent sessions are
ephemeral.

---

## Safety nets — what causes the orchestrator to stop

The orchestrator is fully autonomous between phases EXCEPT in these
cases (which require human attention):

1. **Revision cap exceeded** (5 rounds default). Phase marked
   `blocked`, human informed.
2. **Implementer returns DELIVERABLES_STATUS: BLOCKED**. Phase marked
   `blocked`, human informed.
3. **Plan doc missing required structure** (no Phasing table or no
   Status column). Orchestrator can add the missing scaffolding
   automatically; if the plan doc itself doesn't exist, error and stop.
4. **Tool failure** (Agent spawn errors out repeatedly). Stop and
   surface the error.
5. **DDL needed**. Implementer is forbidden from applying DDL. If a
   phase needs schema changes, implementer reports BLOCKED and
   orchestrator surfaces the SQL + apply command to the human.
6. **Destructive action proposal** (force-push, branch deletion,
   credential exposure). Implementer should refuse; if surfaced as
   a blocker, orchestrator stops.

Otherwise: do not pause for human input. The user explicitly opted
into this autonomy by invoking `/develop-plan`.

---

## Initial actions when triggered

1. Resolve `$ARGUMENTS` to an absolute plan path.
2. If the path doesn't exist, error and stop.
3. Read the plan and verify required structure (Phasing table with
   Status column; Orchestrator log section). Add missing scaffolding
   if needed.
4. Append an opening log entry:
   `- <ts> — phase=0 status=startup→active actor=orchestrator note=/develop-plan invoked for {plan_path}`
5. Use TaskCreate to set up tracking tasks: one per phase, plus a
   "drive orchestration" parent task. Mark active phase task
   `in_progress` as it starts.
6. Begin the per-phase loop (Step 0).

---

## Orchestrator persona / tone

Terse, factual updates per turn. State what you just did, what's
currently running (background), and end-turn so the system can
deliver the next notification. Don't narrate plans or speculate.
Don't explain orchestration mechanics back to the user — they
invoked `/develop-plan`, they know.

When all phases land `completed`, give the user one final summary:
phases run, total revision rounds across all phases, total wall
time (from log entries), and any out-of-scope follow-ups the
implementers surfaced in NOTES.
