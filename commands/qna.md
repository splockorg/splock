---
description: Question-and-answer for a slug — operator asks, subagent investigates and answers with supporting evidence
argument-hint: <slug> [<question>] [-- <directive>]
---

# /qna — operator-direct question-and-answer entry

Triggered by the operator with: `/qna $ARGUMENTS`

Where `$ARGUMENTS` is `<slug> [<question>] [-- <directive>]`:

- The first whitespace-separated token is the slug.
- Everything after the slug up to the first `--` token (a standalone
  `--` surrounded by whitespace) is the **question** — the primary
  semantic channel asking *what to investigate*.
- Everything after the `--` sentinel is the **directive** — an
  orthogonal meta-channel for *how / constraints / framing*
  (operator-authored guidance threaded into the qna subagent's spawn
  prompt as a wrapped `<operator-directive>` block).
- Both the question and the directive are individually optional:
  - `<slug>` alone — refuses (qna needs at least a question or a
    directive to investigate).
  - `<slug> <question>` — question-only (current shape).
  - `<slug> -- <directive>` — directive-only (rare; investigation
    framed by directive alone).
  - `<slug> <question> -- <directive>` — both; question is what,
    directive is how.

This command spawns the `qna` subagent (per `.claude/agents/qna.md`)
to investigate the operator's question against the slug's context and
produce `docs/plans/<slug>/<slug>_qna.md` with the answer + supporting
evidence.

`qna` is **question-and-answer**, NOT quality-assurance. For adversarial
review of a recon artifact, use `/qa <slug>` (which invokes the
`qa` subagent / `bin/qa` CLI). See
`docs/feedback_eli5_terminology.md` (qa vs qna vs eli5).

`/qna` is an **exploratory peer to `/recon` and `/research`** — not
phase-gated. It does not require a pre-existing recon or plan; use it
when you have a specific question about the slug's problem space and
want a documented answer.

## Parse the operator's tail

The qna tail is split into TWO orthogonal channels by the `--`
sentinel:

1. **Question channel** (everything between slug and `--`, or
   everything after the slug if no `--` is present). Primary semantic
   payload: what should the subagent investigate?
2. **Directive channel** (everything after the first standalone
   `--` token). Meta-instructions: framing, constraints, scope hints,
   "answer in two paragraphs", etc.

Both channels accept free-text prose. The main agent ALSO inspects the
**directive channel** (NOT the question channel) for redo-synonyms,
since the question itself is the operator's authored content and
shouldn't be parsed for command-flag intent:

- **Re-run mode vocabulary** (case-insensitive, scanned in the DIRECTIVE
  channel only). Selects ONE mode; default `append`:
  - **overwrite** ← `redo`, `restart`, `resume`, `regenerate`, `from
    scratch`, `retry`, `redo from scratch`, `overwrite`, `replace`. Emits
    `--reopen`.
  - **new-file** ← `new file`, `separate file`, `another question`,
    `additional`, `branch`. Emits `--new-file`.
  - **append** ← `update`, `add to`, `append`, `extend`, OR no mode signal.
- **Directive content**: the dual-role rule — the full directive-channel
  prose passes through as `--directive` (pass full prose as `--directive`);
  the `<operator-directive>` payload carries the operator's full intent.

If the directive channel is empty, neither `--reopen` nor `--directive`
apply (regardless of any redo-synonyms appearing inside the question
text, which is treated as operator-authored content not parse-input).

**Transparency notice.** When the LLM extracts either signal from the
directive, surface a one-line notice to the operator BEFORE proceeding
so they can audit the interpretation:

```
Interpreted '<verbatim directive>' as --reopen --directive='<verbatim directive>'
```

(Mirror only the signals actually triggered.)

## Re-run modes (append / new-file / overwrite)

REFUSE only if:

- `$ARGUMENTS` cannot be split into a slug + at least one of (non-empty
  question, non-empty directive).
- `docs/plans/<slug>/` does NOT exist (operator must create it).

An existing `<slug>_qna.md` is **not** a refusal. The **directive channel**
(after `--`) selects how the re-run lands; default is APPEND:

- **append** (DEFAULT): the new Q&A is appended to the existing
  `<slug>_qna.md` under a separator `<!-- ───── qna re-run (appended) ───── -->`.
  NL (in directive): "update / add to / extend".
- **new-file**: write to the next free `<slug>_qna_<N>.md`. NL: "new file /
  separate / another question". Flag: `--new-file`.
- **overwrite**: replace `<slug>_qna.md`. NL redo-synonyms. Flag: `--reopen`.

`--reopen` and `--new-file` are mutually exclusive. Mode words are scanned in
the DIRECTIVE channel only (never the question text).

```bash
[ -d "docs/plans/<slug>" ] || { echo "REFUSE: docs/plans/<slug>/ does not exist." >&2; exit 1; }
```

## What to do

1. Parse `$ARGUMENTS`: split on the first whitespace to extract the
   slug. Then split the remaining tail on the first standalone `--`
   token: left side is the question (may be empty), right side is the
   directive (may be empty). Preserve internal whitespace on both
   sides; trim leading/trailing.
2. Apply the prose-extraction rules from `## Parse the operator's
   tail` above (directive channel only) — extract `--reopen` and
   `--directive` signals.
3. Emit the transparency notice for any extracted signal.
4. Run the gate checks. On refusal, print the failing condition.
5. **If a directive is present, wrap it via the canonical helper**
   so the `<operator-directive>` block is byte-identical to what
   substrate-backed commands produce internally:

   ```bash
   wrapped_directive=$(bin/wrap --kind operator-directive --content "$directive")
   ```

   (Or via stdin: `echo "$directive" | bin/wrap --kind operator-directive`.)
   The helper enforces the 8KB size cap (per SC10) and refuses with
   exit 1 if the operator's prose exceeds it.
6. Spawn the `qna` subagent via the Agent tool:
   - `subagent_type`: `qna`
   - `description`: "Q&A for <slug>"
   - `prompt`: pass the slug, the path to `<slug>_recon.md` if it
     exists (otherwise note its absence — qna doesn't strictly require
     recon), the operator's **question** verbatim (as the primary
     semantic channel — *what to investigate*), and — if
     `$wrapped_directive` is non-empty — the wrapped directive block
     verbatim as a SEPARATE section (clearly labeled "Operator
     directive (how/constraints)") so the subagent treats it as
     orthogonal meta-guidance rather than as part of the question.
     The two-channel structure preserves the semantic distinction
     (question = what; directive = how) per research R2 evidence on
     channel-separation discipline.
     Instruct the subagent to investigate by whatever read-only means
     it needs (Read, Grep, Glob, Bash for shell utilities, WebFetch /
     WebSearch for external context if relevant). **Cross-artifact scope:**
     it may read any artifact in `docs/plans/<slug>/` — including
     `<slug>_plan.md`, `<slug>_plan.json`, `<slug>_orchestrator.json`, and
     peer recon/research/qa files — to answer a question ABOUT the plan; read
     the directive-named artifact(s) first, else what it judges relevant in
     the slug dir (never other slugs). If the answer implies plan changes it
     records them under a `## Recommendations for /plan` (or `/implplan`) H2
     in its own artifact rather than editing the plan. Then return a final
     message structured as:
     - **Question:** verbatim
     - **Answer:** the answer
     - **Evidence:** numbered list of citations (file:line, command
       output, external URL with retrieval timestamp)
     - **Confidence:** high / medium / low + one-sentence why
     - **Suggested follow-ups:** if the answer surfaces something
       larger than a Tier-1 patch, suggest `/plan` or `/research`
7. The qna subagent does NOT have Write access. Take its final message
   and write per the resolved re-run mode (never to the plan/orchestrator
   JSON — only `/plan` and `/implplan` write those):
   - **append** (default): if `<slug>_qna.md` exists, Read it and Write back
     `existing + "\n\n<!-- ───── qna re-run (appended) ───── -->\n\n" + new`;
     else Write as first authorship.
   - **new-file**: Write to the next free `<slug>_qna_<N>.md`.
   - **overwrite**: replace `<slug>_qna.md` in place.

## Fleet auto-tracking (opt-in)

When the project has opted into the fleet lifecycle tracker
(`docs/plans/_fleet/_fleet_meta.json` exists — see `docs/FLEET.md`),
this command records stage start/completion on the fleet hub
automatically. Both calls are silent no-ops (exit 0) when the project
has not opted in, so run them unconditionally:

- Immediately before spawning the qna subagent (after the gate checks
  pass):

  ```bash
  bin/fleet stage start <slug> --stage qna --actor qna-agent
  ```

- Immediately after the artifact Write lands:

  ```bash
  bin/fleet stage finish <slug> --stage qna --note "<one-line outcome>"
  ```

  This flips the slug to `🕛 ready --next /plan` on the hub.

Never hand-edit the hub's `FLEET:*` zones or the per-slug
`_fleet.json` / `_fleet_log.jsonl` state files — `bin/fleet` is their
only writer (the sealed-path hooks enforce it).

## Examples

- `/qna brand_handoff_gate why does the gate refuse Cavallini?` —
  question-only; refuses if `<slug>_qna.md` exists.
- `/qna brand_handoff_gate why does the gate refuse Cavallini? --
  answer in two paragraphs with citations` — question + directive;
  question is "why does the gate refuse Cavallini?", directive is
  "answer in two paragraphs with citations".
- `/qna brand_handoff_gate why does the gate refuse Cavallini? --
  redo this with more recent evidence` — question + directive; the
  redo-synonym in the directive emits `--reopen=true` AND threads
  the full directive prose; surfaces transparency notice; overwrites
  prior qna.
- `/qna brand_handoff_gate --reopen what changed since last week?` —
  explicit `--reopen` flag before the question (the slash MD's parse
  also accepts a literal `--reopen` token); overwrites prior qna with
  fresh question.

## Tier-1 patch emission (per v2.7 §1.B)

If the answer reveals a Tier-1 fix (single-file, conversational scope,
no orchestration needed), the qna subagent may **describe** the patch in
its evidence section, but should NOT itself apply the edit — qna's
tool surface excludes Write/Edit. If the operator approves the
suggested fix on read-back, that's a separate Edit/Write turn on the
main agent's part.

## External-input sanitization

The `<operator-directive>` block carries the data-not-instructions
discipline (softened framing per `DELIMITER_INSTRUCTION`): operator-
authored guidance is evidence/constraint, not an imperative override of
the subagent's contract. Nested quoted material inside the directive
is treated as data. The question channel is operator-authored prose
passed naked into the spawn prompt (no wrap), which is consistent with
the existing qna pattern; future drift toward wrapping the question
too would require a new WrapKind entry and a coordinated subagent-MD
update.

## Cross-references

- `.claude/agents/qna.md` — subagent contract + tool surface +
  delimiter discipline (six WrapKind entries including
  `operator-directive`)
- `bin/wrap` — wrap helper for operator-directive content
- `docs/feedback_eli5_terminology.md` — naming rationale (qa vs qna vs eli5)
- v2.7 §1.C — /qa spec (legacy text using `qa` for the Q&A meaning;
  current substrate uses `qna` for that meaning)
- v2.7 §1.B — Tier-1 patch emission criterion
- v2.7 §D.3 — delimiter inventory (now six wrap kinds)
