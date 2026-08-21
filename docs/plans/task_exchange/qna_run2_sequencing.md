# qna run 2 — process and sequencing

**Slug:** `task_exchange` · **Run:** 2 of 2 · **Mode:** append (lands under a
separator in the existing `task_exchange_qna.md`)

Questions 6–8 are about how to attack the work, not what to build. **Run this
after `qna_run1_design.md`** — question 8 in particular wants run 1's answers in
hand, since the overlap between this feature and multi-routing is partly a
design question that run 1 settles.

**Design of record:** [`docs/TASK_EXCHANGE.md`](../../TASK_EXCHANGE.md),
[`docs/TASK_EXCHANGE_DIAGRAMS.md`](../../TASK_EXCHANGE_DIAGRAMS.md), and for
question 8, [`docs/MULTI_ROUTING_ROADMAP.md`](../../MULTI_ROUTING_ROADMAP.md).

**Invoke with:**

```
/splock:qna task_exchange Read docs/plans/task_exchange/qna_run2_sequencing.md and answer every numbered question in it, following the "How to answer" section at the foot of that file. -- Append these answers to the existing task_exchange_qna.md, keeping run 1's answers intact. Cite file:line, command output, or a URL with its retrieval date for every load-bearing claim. Answer each numbered question under its own heading.
```

The `--` directive carries the word "append", which selects append mode
explicitly. `/splock:qna` will echo a one-line notice showing how it read that
directive — check it says `--directive` and *not* `--reopen` before proceeding.

---

## 6. What warrants a `/research` pass, and what are the exact prompts?

Some of this design turns on questions the repo cannot answer — vendor
behaviour, prior art in other systems, protocol choices, failure patterns other
people have already hit. Those want `/research`, not another repo read.

Identify which parts those are, then **write the prompts verbatim and ready to
paste** — one per run. For each prompt state:

- the question it answers, in one line
- what a good answer looks like, concretely enough to know when the run is done
- what sources would count as primary for it

Then say whether they run as one pass or several, and in what order. If two
prompts would return overlapping material, fold them.

Candidate territory, to accept or reject with reasons rather than treat as a
list to fill:

- prior art in distributed work-claiming — lease/heartbeat/requeue patterns,
  where the classic implementations get it wrong at small scale
- what other teams have built for sharing agent capacity, and how those handled
  the credential boundary
- cross-machine handoff of partially-complete coding work: what actually
  transfers, what always has to be redone
- the durability of undocumented vendor endpoints as a dependency, and the
  standard mitigations

## 7. What poll and heartbeat cadence should workers use?

Pick numbers, and ground them. Note that two different things are being polled
and they have different constraints:

- **the exchange tables** — cheap, local-network, no vendor limit
- **the OAuth usage endpoint** — has its own rate limit and returns 429s; the
  reference implementation defaults to 60 s with a 20 s floor and identifies as
  the installed CLI version to stay out of the stingier bucket for unrecognised
  clients

Answer for each:

- What interval, and what is the actual cost of being wrong in each direction?
- What does a stale heartbeat cost the matcher — specifically, how long can a
  dead worker hold eligibility before it distorts an assignment?
- What should lease expiry be, relative to how long a real stage takes? A
  `/code` stage can run for many minutes; a lease that expires mid-stage
  requeues work that is actually progressing.
- Should cadence be adaptive — faster when offers are open, slower when the
  board is quiet — or does that break determinism?
- Where does polling load actually land, and does it matter at five workers? At
  fifty?

## 8. Should multi-routing land before this, or can the exchange go first?

`docs/MULTI_ROUTING_ROADMAP.md` describes routing splock's roles across Claude,
Codex, and Antigravity. It is researched and unimplemented. This feature is
researched and unimplemented. Both answer the same underlying question — "this
stage needs a capability I do not have right now" — from different directions.

The judgement call: the exchange looks like the bigger practical win, but we do
not want to design the same seam twice.

Name the concrete overlaps and take a position on each. At minimum:

- the router seam and `RouteQuery` / `RouteDecision`
- the `ModelTransport` ABC and its capability tags
- the hardcoded `["claude", "-p", …]` argv in `bin/_fleet/spawn.py`, which the
  roadmap already flags as a fourth un-audited transport seam
- the roster schema version, which the roadmap says must be re-cut as v4
- the per-stage `profiles` block, which both features want to read

For each overlap, say whether building the exchange first **creates rework**,
is **neutral**, or actively **de-risks** the routing work. Then give a
recommendation with the rework cost stated in concrete terms — files touched,
migrations needed, interfaces that would have to change shape — rather than as
a general feeling about ordering.

Consider also whether there is a third option: a small piece of the routing
seam that is worth landing first *because* the exchange needs it, without
committing to the whole roadmap.

---

## How to answer

- Ground every load-bearing claim in this repo or a primary source. Cite
  `file:line`, command output, or a URL with its retrieval date.
- Question 6's deliverable is the prompts themselves — write them out in full,
  in a fenced block each, ready to paste without editing.
- Question 7's deliverable is numbers with reasons, not a range.
- Question 8's deliverable is a recommendation with its cost stated.
- Answer each numbered question under its own heading, in order.
