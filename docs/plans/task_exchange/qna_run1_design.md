# qna run 1 — design interrogation

**Slug:** `task_exchange` · **Run:** 1 of 2 · **Mode:** default (creates
`task_exchange_qna.md`)

Questions 1–5 interrogate the design itself. They share context, so they belong
in one pass. Run 2 (`qna_run2_sequencing.md`) covers process and ordering and
should run *after* this one — question 8 there wants these answers in hand.

**Design of record:** [`docs/TASK_EXCHANGE.md`](../../TASK_EXCHANGE.md) and
[`docs/TASK_EXCHANGE_DIAGRAMS.md`](../../TASK_EXCHANGE_DIAGRAMS.md). This slug
has no recon and no plan yet — those two documents are the only prior art.

**Invoke with:**

```
/splock:qna task_exchange Read docs/plans/task_exchange/qna_run1_design.md and answer every numbered question in it, following the "How to answer" section at the foot of that file. -- Cite file:line, command output, or a URL with its retrieval date for every load-bearing claim. Answer each numbered question under its own heading. Where a premise cannot be verified, say so plainly rather than answering around it.
```

---

## 1. Is the exchange pathway more complicated than it has to be?

Diagram 1 in `TASK_EXCHANGE_DIAGRAMS.md` routes work through three moving
parts: a shared table, a git remote, and a per-operator cron. Some of that
structure is forced by the compliance boundary; some of it may be incidental.

Separate the two. For each moving part, say whether removing it would breach a
redline, break an invariant, or merely cost convenience. Test the obvious
collapses explicitly:

- Could git carry the whole exchange — offers as branches or as files in a
  well-known ref — with no database at all?
- Could the table carry the work, with no git remote in the loop?
- Could the cron be replaced by something event-driven, or is polling load-bearing?

Finish with the simplest design that still holds all five invariants, and name
what each simplification costs in determinism, auditability, or redline distance.

## 2. How does the origin developer interface with work running on someone else's machine?

Once an offer is executing on another dev's box, the origin dev will need to
reach it: supply missing context, answer a question the worker raises, cancel
the run, or just see progress. The design currently says almost nothing about
this, and it is the part the origin developer will feel every day.

State the rules first — the offer is untrusted input in *both* directions, and
the executing worker applies its own policy, so what may legitimately flow back
and forth, and what must not?

Then compare concrete channels and rank them **by effort for the origin
developer specifically**, not by architectural elegance:

- GitHub issues or PR comments on the returned branch
- a markdown file moved through the repo (the pattern splock already uses)
- rows in the exchange tables, surfaced through `bin/fleet`
- direct session re-entry on the worker's side (`bin/fleet resume`, which
  already re-enters a headless child by session id with full context)

For each: latency, auditability, what it costs in trust, and whether it works
when the origin developer is asleep. Recommend one, and say what makes the
runners-up wrong rather than merely worse.

## 3. What should happen at the moment of handoff, before anyone else picks it up?

An offer gets minted exactly when the origin stalls — the 5-hour window closes,
the weekly Fable limit hits 100%, whatever the cause. At that moment the origin
still holds all the context, and the receiving dev holds none. The receiving dev
should not have to rediscover what is already known.

So: what should a pre-handoff pass actually do? Enumerate what belongs in the
offer envelope beyond the routing fields — known blockers, what was already
tried, which tests fail and how, what the next concrete step is.

Then audit what splock already has that does some of this:

- the retry-loop boundary briefing (`bin/_retry_loop/`)
- `bin/fleet update <slug> --spawn-directive` and its one-shot semantics
- fleet's `blocked` status and its blockers list
- the `_fleet_runs.jsonl` result envelopes

Name what is reusable, what needs building, and what is already generated but
not currently reaching an offer.

One sharp sub-question: that pre-handoff pass spends tokens, at exactly the
moment the origin has none left. Say plainly whether it conflicts with
Invariant 1, or sits outside it — and if the origin genuinely cannot afford it,
what the fallback is.

## 4. Where does the model/effort recommendation live, and how does it travel?

Recent fleet work put model and effort suggestions on queued agents:
`roster.<slug>.attended {slot, model, effort, ultracode}` and the per-stage
`profiles` block in `_fleet_meta.json`. The exchange needs the same information
as `required_model` and `min_effort` on an offer.

Find where this lives today, precisely, and answer:

- Is the exchange's requirement the same mechanism, or a second one that would
  drift from the first?
- What would it take to harden the recommendation so it travels with *any*
  handoff, rather than only inside fleet?
- What is the single source of truth, and what has to move to make it so?
- What decides the recommendation — is it operator-authored, derived from the
  stage, or derived from what the work turned out to need?

Note the interaction with question 3: a stage that just failed on a weaker model
is evidence about what the next attempt needs, and that evidence is generated at
handoff time.

## 5. Is anything non-deterministic that should not be — especially in the cron and usage path?

Walk the design for anything a cron does that two machines could do differently:
clock reads, ordering, tie resolution, retry timing, anything that folds a
floating-point number, anything that reads local state the other machine cannot
see.

The usage-tracking path deserves specific attention, and the premise here is
**confirmed, not assumed**: `docs/TASK_EXCHANGE.md` Invariant 3 documents a
zero-token quota read at `GET https://api.anthropic.com/api/oauth/usage`,
authorised with the `accessToken` already in `~/.claude/.credentials.json` plus
the `oauth-2025-04-20` beta header. It was verified live on 2026-08-21 and
returns a `limits[]` array carrying `kind`, an integer `percent`, a `severity`,
`resets_at`, and for scoped rows `scope.model.display_name`. A working
reference implementation exists on Bill's machine — ask him for
`~/bin/claude-usage` rather than rewriting it.

Given that, answer:

- The endpoint is **unofficial and undocumented**. How much of the matcher may
  legitimately depend on it? What is the honest failure story when the payload
  shape drifts, the endpoint 429s, or it disappears?
- The reference implementation walks the response tree rather than hardcoding
  keys, because the live payload carries a dozen mostly-null codename fields.
  Is `limits[]` a stable enough contract to key a schema on?
- It identifies as `claude-code/<installed version>` because unrecognised
  clients get a stingier rate-limit bucket. Is that acceptable to depend on, and
  what breaks if it stops being?
- Which parts of this whole system should be plain shell rather than an agent
  turn? Be specific about where a token is currently implied but not needed.

---

## How to answer

- Ground every load-bearing claim in this repo or a primary source. Cite
  `file:line`, command output, or a URL with its retrieval date.
- Prefer one recommendation with its cost stated over a survey of options.
- Where a question rests on a premise that cannot be verified, say so plainly
  instead of answering around it.
- Answer each numbered question under its own heading, in order.
- Where an answer implies a change to `docs/TASK_EXCHANGE.md`, name the section
  and what it should say instead — the design of record should not be left
  contradicting a finding.
