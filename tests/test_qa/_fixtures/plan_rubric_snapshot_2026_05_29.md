# qa rubric — adversarial review of `<slug>_plan.md`

Read the plan end-to-end. The plan.json is already schema-valid by
Call-2 constrained decoding, so review SEMANTIC quality, NOT schema
validity: is the task decomposition sound and collectively sufficient
for the success criteria? does every success criterion have a
verification path? are the `depends_on` edges acyclic and complete? is
the declared tier proportionate to the blast radius? does the solution
actually address the problem statement? Frame every finding as a
recommendation for the next plan revision — the operator drives the
re-plan; you do not author it.

**Output discipline:**

1. Markdown. No JSON, no code-block-only output.
2. Cite the plan section/paragraph each question challenges, e.g.
   "(§4.2(b), line 244)". This is the plan's section + approximate
   line — not a verified-against-tree citation; the planner will
   re-verify if needed.
3. Questions are NOT answered here. Surface them for the planner /
   research subagents to address before Call 2 emission.
4. Organize findings into four blocks (A/B/C/D below). Use H2 for the
   block headings. Use bold inline tags (`**A.1.**`, `**B.3.**`) for
   individual findings.
5. Do not invent file paths or function names not in the plan. If a
   citation in the plan looks suspicious, flag it under Block B as
   "verify line citation against current tree" — this CLI substrate
   runs SDK-direct without tool access, so claim-against-tree
   verification is the planner's job at Call 1 re-verification time.

## Block A — Task-decomposition soundness + test-coverage-per-criterion

Are the `tasks_skeleton` tasks atomic, well-scoped, and collectively
sufficient to satisfy the success criteria? Are there success criteria
with no task covering them, or tasks not tracing to any criterion?
Does each success criterion have an identifiable acceptance /
verification path (a test, a check, an observable), or is it
aspirational? This is the highest-priority plan check. Findings here
are recommendations for the next plan revision (semantic, NOT schema —
the plan.json is already schema-valid by construction).

For each item under Block A, include:
- The task or success criterion (with its id, e.g. T3 / SC2)
- The coverage gap (criterion with no task, task tracing to nothing, or
  criterion with no verification path)
- A recommendation for the next plan revision

## Block B — Dependency correctness

Are the `depends_on` edges acyclic, and do they reflect real
prerequisite ordering? Flag missing edges (a task uses an artifact a
prior task produces but does not declare the dependency) and spurious
edges. These are load-bearing for the orchestrator's task ordering.

For each item under Block B, include:
- The two task ids and the edge in question
- Why it is missing, spurious, or cyclic
- The corrected edge recommended for the next plan revision

## Block C — Scope-vs-tier proportionality + reference integrity

Is the declared `tier` (1/2/3) consistent with the blast radius implied
by the tasks + architecture? Flag a Tier-1 label on a multi-module
plan, or vice-versa. Are `non_goals` deferrals named and routed
(follow-on slug / scheduled marker) rather than silently dropped? Do
`references[]` point at artifacts that exist in the slug dir, and is
the `kind` enum honest?

For each item under Block C, include:
- The declared tier / non_goal / reference (with its location)
- The proportionality or integrity concern
- A recommendation for the next plan revision

## Block D — Problem-statement coverage

Does the plan's solution actually address the stated problem, and does
`conceptual_architecture.overview` cohere with the success criteria?
Flag any part of the problem statement the success criteria leave
unaddressed.

For each item under Block D, include:
- The unaddressed (or under-addressed) part of the problem statement
- Which success criteria should have covered it but do not
- A recommendation for the next plan revision

---

**Closing note:** if the plan is high-quality and you find few or no
issues in a block, say so explicitly ("Block C — dependency graph is sound"). An empty block is signal; do not pad with low-value findings.
