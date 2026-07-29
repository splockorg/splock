# splock — Vision (Bill's statement, v1.1, 2026-07-28)

> **This document supersedes the v0.1 draft.** It is the program's
> **first-principles reference**: every plan, prompt, schema, hook, and code
> change must be traceable to a clause in here.
>
> **Rule for planning and coding agents:** if the work you are about to do
> cannot be traced to this document, it is drift — stop and ask the operator.
> Extending this document is a human decision, not an agent decision.

---

## 1. What splock is

**splock is *specification lock*.**

The name is the thesis. A specification — intent, requirements, vision,
engineering decisions — is only worth writing if it is **locked**: held in a
form the agent working under it cannot reinterpret, quietly amend, or outgrow.
An unlocked spec is a suggestion, and an agent handed a suggestion drifts. Not
maliciously — a capable agent drifts because reinterpreting the spec is usually
the shortest path to something that looks finished.

So splock is **a governed plan → implement → verify lifecycle for agentic
coding, with a deterministic enforcement spine.** Locking is the whole product;
the lifecycle is what locking looks like when it is applied end to end.

Four capabilities, one loop:

| Capability | What it does |
|---|---|
| **Govern** | Turns an initiative into an explicit, schema-valid plan of record before any code is written, and holds the work to it. |
| **Enforce** | Moves every load-bearing boundary out of the model and into hooks and CLI exit codes that execute outside it. |
| **Verify** | Separates the agent that does the work from the agent that judges it, and pins the judge so the doer cannot steer it. |
| **Operate** | Lets one human run many agents across many slugs concurrently, from one screen, without the agents clobbering each other or the record. |

The first three are splock v1. The fourth (`fleet`) is what the framework grew
into once v1 was real, and it is now co-equal — not an add-on.

## 2. What splock is not

The positioning is as load-bearing as the architecture, because most of what
gets called "agentic development tooling" is solving a different problem.

- **Not one-shot vibe programming.** splock works inside **Spec-Driven
  Development cycles** and only there. The unit of work is a planned
  initiative, not a prompt. Below a certain size splock is pure overhead, and
  that is the correct trade — a framework that tries to also be frictionless
  for a two-line fix will be frictionless for a two-week build too, which is
  where the drift lives.
- **Not a PRD workflow.** A Product Requirements Document is a product
  artifact, written for humans, in prose, at a level of abstraction that
  deliberately does not decide implementation. splock locks **engineering
  decisions** — scope, file boundaries, task DAGs, success criteria, test sets.
  Heavy PRD-first approaches to agentic programming put the least enforceable
  artifact at the top of the stack; splock puts the most enforceable one there.
- **Not an issue tracker, and not an integration with one.** No Jira, no GitHub
  Issues, no external system of record. The plan of record and the deferred-work
  ledger (§9) live in the repo, next to the code they govern, and travel with
  it.
- **Not a service.** Standalone and lightweight: no server, no account, no
  hosted state, no network dependency at the core. A developer adds it to a
  repo and it works.

Stated positively: **splock is what a developer uses to lock in engineering
decisions and then build a complex system against them.**

## 3. Who it is for, and the promise

The user is **one operator running semi-autonomous coding agents against real
codebases** — often several at once, often unattended, often overnight.

The promise, stated as the product bar: **a workflow you can hand to an
autonomous agent and still reason about what it is structurally prevented from
doing.** Not "what it was told not to do" — what it *cannot* do.

Two resources are scarce, and they are not the same resource:

- **The operator's scarce resource is attention.** Anything that forces a human
  to re-read state, re-derive "what's next", or babysit a run is a defect in
  splock, not a cost of doing business. This is why derived state is generated
  (§4.5), why fleet exists (§10), and why overnight mode front-loads the
  operator's questions into one bounded window instead of scattering them
  across a night (§11).
- **The system's scarce resource is subscription pool headroom.** Not dollars —
  splock never spends metered dollars (§4.11) — but the weekly capacity of the
  operator's plans across model providers. This is why routing exists (§12).

A consequence of both: **splock is measured on unattended runs.** A feature
that only works when a human is watching has not shipped.

## 4. First principles (the invariants)

1. **Prose cannot enforce a boundary.** Anything written for a model to read —
   a system prompt, a skill, an agent contract, a file the agent retrieves —
   can be reinterpreted by the model or overridden by adversarial content it
   ingests. Enforcement lives *only* in code that executes outside the model:
   lifecycle hooks and CLI exit codes. Treating agent prose as an enforcement
   mechanism is the original sin. (SPEC §0.1, DESIGN §1)
2. **Self-certification is not verification.** The agent that does the work
   never decides whether the work is done. The judge runs on a fixed, dated
   model that the operator may not override, because a tunable judge is a
   steerable judge. (SPEC §0.2, DESIGN §4)
3. **A written plan precedes code, and the plan of record is sealed.** This is
   the lock. The substrate cannot be raw-edited; surgical changes go through an
   amend path that re-validates and re-renders, so the human-readable twin can
   never drift from the machine-readable truth. No agent silently rewrites the
   spec it is held to. (DESIGN §2)
4. **State is mutated only through a CLI.** Validated against schema, legal
   transitions enforced, appended to a log. State files are the single source
   of truth for where a build is, and they are sealed against the agents that
   produce them. (SPEC §S)
5. **Derived state that is not generated will rot.** Anything computable from
   per-slug state is a generated zone; narrative, charter, and operator rulings
   stay human. The field evidence is on the record: a hand-authored prompt bay
   rotted within a day. (FLEET.md — prompt bay, closeout)
6. **Contention is designed out, not locked around.** One writer per resource,
   per-slug files joined at render time, atomic swap, append-only logs under
   `PIPE_BUF`. Concurrency safety is a data-layout property, not a mutex.
   (FLEET.md — safety properties)
7. **Fail closed, and fail loudly.** A missing marker, an unreadable
   `/dev/tty`, an unknown schema version, a failed re-validation, an
   overlapping intent claim → refuse. And a gate that could not actually be
   evaluated is **not** a pass: a pre-exhausted run diagnoses instead of
   passing; an orchestrator that commits to no work at all is refused.
   **Vacuous green is a defect class of its own**, distinct from a bug.
8. **Every verdict leaves a receipt.** A status flip with no artifact behind it
   is an assertion, not evidence — precisely the objection splock raises
   against a coder that says "done". The same rule binds an unattended run: a
   decision the operator would have made, made without a record, is the same
   defect (§11). This holds today on the red path and not on the green path;
   that gap is an invariant violation, not a missing feature. (§16.1)
9. **Defense in depth, with the hard boundary named.** Tiers are ordered
   explicitly — platform floor, then OS-level hardening, then hooks, then CLI
   exit codes — and the doctrine states which tier is load-bearing, so a
   weakness in a convenience tier is never read as a hole in the model. Prose
   is never a tier.
10. **The agent may never edit splock's own plumbing.** Hooks, the sealed
    inventory, schemas, and plugin manifests are sealed against every agent,
    including splock's own. A sanctioned human write path may exist, but it is
    hook-gated, audit-logged, TTY-confirmed, and unreachable headless. (FO-1)
11. **Subscription transport, never a metered key.** Children spawn as CLI
    subprocesses on the operator's subscription; `ANTHROPIC_API_KEY` is never
    read or required. This is a billing-model constraint on the architecture,
    not a style preference, and it governs every model family splock ever adds
    (§12). (`bin/_fleet/spawn.py`)
12. **Zero-config value; everything on; disable by choice.** splock runs on its
    defaults with no `.splock.toml`, and those defaults are the *whole*
    framework. An adopter gets value before configuring anything, and
    configuration is how they turn capability **off**, not how they assemble it.
    Every key has an env override; Python is standard library at runtime with
    `jsonschema` optional. What may be switched off — and the two things that
    may not — is §13. (DESIGN §7)
13. **Nothing is silently dropped — deferral is a routed act.** A problem an
    agent finds but was not asked to solve has exactly one legal move: route it
    to a named destination, from a closed set (§9). Deferred scope becomes a
    follow-on (`FO-n`), a field bug becomes an outstanding item (`OI-n`),
    later-work becomes a scheduled marker carrying its own reopen condition.
    **"We decided not to" is recorded with its gating condition; "we forgot" is
    a defect.**
14. **Operator-facing claims are load-bearing.** A doc that overstates what a
    gate does is a defect of the same class as a test that passes when it
    shouldn't — both tell the operator the system is safer than it is. Docs get
    truthed up in the same commit as the behavior.
15. **splock governs splock.** Every change to this framework is planned,
    implemented, and verified through this framework. Dogfooding is the primary
    evidence that the lifecycle works.
16. **Public-artifact hygiene.** No personal identity, no host identity, no
    private-repo provenance in the published tree — enforced by a grep gate at
    zero, not by review. (`tests/trace_grep.sh`, DESIGN §8)

## 5. The lifecycle, and one vocabulary

```
recon / research / qna  →  plan  →  implplan  →  code (+ test)  →  review / qa  →  close
```

Each stage is a slash command, backed by a skill carrying the model-side
procedure, backed — wherever correctness is at stake — by a deterministic
`bin/` engine the skill routes through. The pattern is the point: **prose
describes, code decides.**

Stages produce durable artifacts, not chat: a recon report, a Q&A log, a sealed
plan substrate plus its Markdown twin, an orchestrator DAG, a verdict, a
closeout. The record survives the session.

**Two mechanisms sit alongside the stages rather than in the sequence.** Both
are load-bearing, and neither is a stage:

- **wrap** is the trust boundary for content entering the plan record. Findings
  from recon, research, qna, qa, and lessons — and operator directives — are
  wrapped in a canonical delimiter pair over a closed kind enum before the
  planner ingests them. It is the mechanism by which §14's "content the agent
  merely reads" stays data instead of becoming instruction.
- **close** is the terminal transition of a slug: final event, archive, meta
  reconcile, successor mint, and one render — atomically, or not at all.

Both ship as CLIs and neither has an agent-facing surface today. §16.13 records
that gap.

**One vocabulary.** Three similarly-shaped names have three disjoint jobs, and
they mean the same thing in this document, in the commands, in the agents, and
in the code. No layer invents a synonym.
(`docs/feedback_eli5_terminology.md`)

| Term | Job | Produces |
|---|---|---|
| **qa** | adversarial review of an existing artifact against a rubric | **problems** |
| **qna** | investigation of an operator's question | **answers** |
| **eli5** | translation of existing material into plainspeak | **nothing new** — it re-expresses |

The eli5 clause carries the load: it must not add findings, drop caveats, or
change substance. A claim it cannot verify is carried as "not independently
checked" — never asserted, never omitted.

The same closure rule applies to the lifecycle's own status words (`wip` /
`ready` / `done` / `blocked` / `parked` / `closed`): it is a **closed
vocabulary**, and no surface may coin a sixth.

**One collision is named rather than tolerated.** Two unrelated mechanisms
both want the word *routing*, and both already carry a `RouteDecision` in code.
They are distinguished everywhere: **issue routing** decides *where a found
problem goes* (§9); **model routing** decides *which family serves a role*
(§12). Neither is ever called simply "routing" in a context where the other
could be meant.

## 6. The enforcement spine

The deterministic core is a set of lifecycle hooks plus the exit codes of the
`bin/` CLIs. A refusal is a non-zero exit code; the model never sees a path
around it because the decision happens in the host, not the conversation.

What the spine refuses, as a closed list of intents:

- writes or deletes to sealed state — plan, orchestrator, chain, fleet, intent
  journal, baselines, secrets, and splock's own plumbing;
- edits that would weaken a test or silence a check (suppression patterns);
- risky package installs and raw schema DDL from a bash call;
- overlapping path claims between concurrent sessions (halt by default);
- runaway output (the lazy-dump cap);
- commits below the evaluation-gate threshold, absent a loud, reasoned
  override.

**Defense in depth is explicit:** the sealed inventory is enforced both as
JSON-deny hooks and as settings-level deny rules, because one layer has a known
gotcha and the other catches it.

**The seam that makes a sanctioned exception possible** is that hooks fire on
the Edit/Write/Read/Bash tools, not on file I/O inside a Python process.
`bin/plan --amend` is the canonical precedent: a privileged, audit-logged,
re-validating mutation reachable only through a CLI, while raw edits stay
denied with a message that routes the agent to the CLI. **Every future
privileged path is modeled on it.**

## 7. The completion gate, and the pin

For each task: the coder writes within its declared file scope and runs the
task's enabled tests; it iterates on failure up to a bounded cap; an
independent verifier judges readiness. Only a READY verdict predicated on a
green run advances the task. Tampering tripwires run alongside — editing a test
file, matching a suppression pattern, or touching a sealed path is detected and
recorded, so a run made green by gutting the test does not pass.

**The pin is the load-bearing part.** If the verifier ran on whatever model the
executor happened to use, an executor could in principle steer its own judge.
The verifier model is therefore fixed in frontmatter and deliberately **not** an
adopter knob — **the one configuration surface splock refuses to open**, and the
one role model routing never touches (§12).

## 8. State, schemas, and the plan of record

- The plan substrate is sealed; the Markdown twin is regenerated in lockstep,
  so the human-readable plan can never diverge from the machine-readable one.
- Every structured artifact has a JSON Schema. Unknown schema versions are
  refused loudly rather than silently accepted, so format drift cannot slip
  through.
- Logs are append-only; state transitions are legal-set-enforced; writes are
  atomic.
- Validation prefers `jsonschema` and falls back to a hand-rolled structural
  check, so the tooling has no hard third-party dependency.
- **The receipts rule (§4.8) is a schema obligation, not a convention:** every
  verdict-bearing stage defines the artifact it must leave behind on *both* the
  pass and the fail path, and its absence is a validation failure.

## 9. Deferred work is routed, never dropped

A framework that refuses a lot needs somewhere for the refused thing to go.
**Fail-closed without a routing path is just attrition** — the agent hits a
wall, the finding evaporates, and the operator never learns it existed. §4.13
is the invariant; this section is the machinery, and it is as load-bearing as
the gate.

The governing rule: **an agent that finds a problem it was not asked to solve
has exactly one legal move — route it.**

### The four destinations, and the one that is not a choice

Routing is a closed, exhaustive enum. There is no fifth bucket and no "note it
in the summary":

| Destination | For | Effect |
|---|---|---|
| **fix-now** | small enough that deferring costs more than doing | done inside the current task |
| **outstanding** | a real defect, not this task's job | one appended line in the outstanding ledger, with a minted id |
| **marker** | work that should happen *later*, on a condition | a scheduled marker carrying its reopen trigger |
| **tier-promote** | big enough to be its own initiative | becomes a plan slug of its own |

If none of the four fits, the CLI **refuses** — because a problem that fits no
destination is mis-shaped, and the correct response is to re-examine it, not to
invent a category. The enum being exhaustive is what makes it enforceable.

**Escalation is not a fifth category — it is a forced outcome.** A closed set
of triggers is evaluated *before* the rubric ever runs, and each is detected by
CLI or hook, never by agent narrative judgment: blast radius over threshold,
multi-column DDL in scope, a change spanning more than one vertical, any path
escaping the repo root, a pending state transition that requires an operator
override. When a trigger fires, the routing decision is taken away from the
agent entirely. This is §4.1 applied to triage: **an agent must not be able to
reason its way past an escalation by choosing a tidier category.**

### What belongs here — and what does not

The mechanism's failure mode is not under-use. It is **an agent routing work out
of a slug because routing is cheaper than doing it.** Markers and outstanding
items are not an overflow bin and not a polite way to say "not me". A ledger
that absorbs anything inconvenient stops meaning anything, and worse, it lets a
slug look finished while its real work sits in a file nobody is scheduled to
read.

**The default destination for anything found inside a slug is that slug.** Work
that belongs to an initiative stays with the initiative. Routing *out* is the
exception, and it requires a reason of a specific kind. "This is tedious",
"this is outside my current task", and "this would be a big change" are not
those reasons — they describe the slug's own backlog, and the slug's plan is
where they get answered.

Exactly two reasons justify moving work out of a slug:

| Destination | The reason that justifies it | The test to apply |
|---|---|---|
| **marker** | a **genuine prerequisite** — the work cannot proceed until something specific becomes true, and that something is outside this slug's control | *Can you name the condition? And would leaving it here hold the slug hostage to a pace the developer does not set?* |
| **outstanding** | **irreducible uncertainty** — something is known to be wrong or needed, and cannot yet be specified well enough to plan | *Could you write a task for it today? If yes, it is not an outstanding item.* |

One line each: **a marker is blocked work with a named trigger; an outstanding
item is unplannable work with an honest admission.** Work that is neither —
merely later, larger, or duller — belongs in a slug. If it is large enough to
deserve one of its own, that is what **tier-promote** is for, and promotion is
the honest move where a marker would be an evasion.

**Why markers are trigger-based by construction.** A marker's trigger is not
scheduling metadata attached to a note; it *is* the prerequisite, written down.
That is why an open-ended trigger is refused at creation, and why declining to
state what must be known before closure requires an explicit operator flag: a
marker whose prerequisite cannot be named is not blocked work, it is abandoned
work wearing a better label.

**Volume is the signal to watch.** A slug that mints several markers is
describing either a real dependency problem worth surfacing or an agent taking
the easy exit — and those two look identical in the ledger while looking nothing
alike in review. The mechanism is meant to be used rarely and precisely, and its
health is measured by how little it is used, not how much.

### Minting discipline

The conventions come from the repo where this mechanism has actually run at
scale, and they are portable by design — they assume nothing about the codebase
they govern:

- **A prefix is a domain, registered before use.** Short caps prefixes, one
  domain each, no compound prefixes. A new prefix enters the registry in the
  **same commit** as the first marker that uses it, so the taxonomy never trails
  the ledger.
- **Sequence numbers are never reused.** A closed marker's id is burned, so
  every reference stays resolvable forever.
- **Closed markers stay.** Closure moves the entry to the archive with its date
  and a written resolution. Nothing is deleted, and the history remains the
  argument for why the work was deferred.
- **Names may be forward-declared.** Reserving ids for work already known to be
  coming — names registered, content deferred — is legitimate and keeps a series
  legible.
- **Markers graduate.** When a trigger fires and the work becomes plannable, the
  marker becomes a slug and records where it went. The lineage is never broken,
  in either direction.

### Scheduled markers — deferral with a reopen condition

A marker is the answer to "not now" that cannot rot into "never". It is a
schema-bound row, not a note:

- **A registered id.** A short caps prefix plus a sequence, with the prefix
  minted in a registry — so a marker's origin is legible years later.
- **A trigger family**, from a closed set: a **date**, a **condition** on
  state, or a **closure trigger** fired by an edit to the code it concerns.
  This is the field that makes a marker different from a TODO — it names *what
  must become true* for the work to reopen.
- **What must be known before it can close.** Deferring without recording what
  evidence would settle the question is the field's most common failure, so
  "n/a" here requires an explicit operator flag rather than passing silently.
- **A detail file** for anything richer than a line, and a **written closure
  resolution** — closing a marker requires saying what actually resolved it,
  not just flipping a status.
- **Who created it**, from a closed enum, so a marker minted by an agent is
  distinguishable from one an operator filed.

Two disciplines make the mechanism honest, and both are vision-level rather
than implementation detail:

- **Nothing may sit forever.** A trigger-based marker still carries a fallback
  reassessment date. A condition that never fires is not a reason for work to
  become invisible; it is a reason to look again on a known worst-case interval.
- **A deferral must name a thing to do.** A marker whose title is a question or
  a speculation is refused at creation. "Should we revisit the cache?" is not a
  deferral, it is an unanswered thought — and unanswered thoughts belong in a
  qna, not in the ledger of committed future work.

### Outstanding items — the capped ledger

Outstanding items are the append-only line log of known defects that are not
this task's job. Two properties matter:

- **It is capped.** The ledger cannot become a dumping ground; past the cap the
  CLI refuses the append. A system that makes filing free makes filing
  meaningless, and an unbounded backlog is indistinguishable from no backlog.
  The cap is a forcing function, not a limitation.
- **Promotion preserves history.** An outstanding line that turns out to be
  real work is promoted into its own plan slug: the origin line does not
  disappear, it flips to *promoted* and gains a pointer to where the work went.
  Nothing is retyped and no lineage is broken.

### Why this is a first-class product surface

The deferred-work ledger is the second thing a returning operator reads, after
the fleet board. It is the reason splock can afford to be strict: **every
refusal has a named destination**, so strictness costs the operator nothing but
a routing decision, and the record of what was consciously not done is as
durable as the record of what was.

## 10. fleet — operating many agents from one screen

fleet answers a different question than the gate: not "is this task done
right?" but **"where does everything stand, and what do I run next?"**

- **Per-slug state, joined at render time.** Every write target is per-slug, so
  any number of agents update concurrently with zero contention. The hub is a
  derived view, never hand-edited, and the per-slug files are sealed so it
  cannot become one.
- **Running the lifecycle *is* the bookkeeping.** Stage engines record `wip` on
  start and `ready --next <stage>` / `done` / `blocked` / `closed` on
  completion. Agents never have to remember the tracker exists.
- **Headless C&C.** One parent forks fresh headless sessions, one per task,
  each with its own model/effort/permission profile, on the operator's
  subscription. The parent absorbs each child's final JSON result — a few KB —
  never its context. Blockers centralize onto one board with copy-paste resume
  handles, and any child is re-enterable with its context intact.
  **Fresh-context-per-task and nothing-ever-lost, simultaneously.**
- **Next actions are generated, not written.** The prompt bay renders a runnable
  spawn line per ready slug; the per-slug directive that makes a spawn
  self-contained is stored once in state and applied by `spawn` itself, so a
  pasted line can never carry stale config.
- **Attended-only is policy, not accident.** Stages on the deny-list render as
  attended gestures and `spawn` refuses them outright.

### Investigation is addressed to the fleet, not to a slug

The upstream stages — **recon, research, qna** — are addressable to the *fleet*
rather than to a single named slug. The reason is a sequencing problem the
current shape gets backwards: **the operator has to name the slug before the
investigation, when the investigation is the thing that reveals which slug the
work belongs to.**

Under a fleet address, slug assignment becomes an **output** of the stage, not
an input:

- **The stage determines where its result lands.** It reads fleet state and
  assigns its findings to the slug they actually pertain to.
- **A result may span several slugs, and the split is the feature.** A qna
  carrying five questions may answer across three slugs; the stage divides the
  work, files each answer where it belongs, and records the split so the
  division is auditable rather than implied. Today that division happens in the
  operator's head, which means it happens inconsistently and is never written
  down.
- **A finding that fits no existing slug is not dropped** — it enters the same
  four-way routing as any other unasked-for finding (§9). It becomes a new
  slug, an outstanding line, or a marker. There is no path where investigation
  output evaporates because it had nowhere to go.
- **Assignment is derived, not hand-picked** (§4.5). The operator does not
  pre-sort their questions by slug, which is exactly the kind of re-derivation
  §3 calls a defect.
- **Ambiguity fails closed** (§4.7). If assignment is not determinable with
  confidence, the stage halts and asks rather than guessing. Filing a correct
  answer under the wrong slug is worse than not filing it: it is drift wearing
  a clean audit trail, and it will be trusted precisely because it looks filed.

**fleet's terminal ambition:** the *only* things a human authors are (a) the
initiative, (b) rulings at junctions, and (c) narrative. Everything else —
status, next action, tree, queue, closeout, and now the assignment of
investigation to slugs — is generated. Measure fleet against the shrinking of
the hand-authored surface.

## 11. Overnight mode — the unattended run

Overnight mode exists at both scales: a single slug under regular splock, and
an entire fleet. The goal in both cases is the same — **carry the work as far
as it can go without the operator present** — and it is the mode §3's
"measured on unattended runs" is really about.

Two mechanisms make it more than "run it and hope".

### The kickoff question window

Every unattended run begins with a bounded, attended window: **the agents get a
fixed span of time to determine every question they will need the operator to
answer.** The operator answers them in one sitting. Then the run goes dark.

This is the attention principle (§3) turned into a mechanism. The failure mode
it removes is the one that makes unattended runs worthless in practice — a
question surfacing at 2am, the run parking on it, and eight hours of capacity
going unspent on a decision that took the operator nine seconds in the morning.
Attention gets spent **once, in a block the operator chose**, rather than
scattered across a night in fragments they were not there for.

It also creates the right incentive. An agent that fails to surface a question
during the window does not get to stop later and ask — it has to live with the
tolerance setting below. Front-loading is rewarded structurally, not requested
politely.

### Blocker tolerance

One dial, answering one question: **what does the run do when it hits something
only the operator should decide?**

| Setting | Behavior |
|---|---|
| **halt** | Stop at the first blocker. The attended posture, and the conservative default. |
| **route-around** | Never decide for the operator. Park the blocked task, keep going on everything still unblocked, and centralize what was parked. |
| **decide-and-log** | Make the call, keep going, and record every operator-grade decision made along the way for review in the morning. |

The exact rungs are the settled shape; whether a fourth belongs between
*route-around* and *decide-and-log* is open (§16.5). What is not open is the
dimension: the dial only ever governs **judgment calls the operator would have
made**.

**The dial moves autonomy. It never moves the gate.** This is the clause that
keeps the mode honest. *decide-and-log* means "decide the product questions I
would have asked you" — it never means pass a task that did not go green, relax
the pinned verifier, skip a test, or reach a sealed path. **No tolerance setting
reaches the enforcement spine.** A tolerance dial wired to the gate would be an
off switch for §4.2 and §4.7, sold as a convenience feature, and it is precisely
the thing splock exists to refuse.

**Every operator-grade decision leaves a receipt** (§4.8). The end-of-run notes
the operator reads in the morning are not a courtesy summary — they are the
artifact that makes *decide-and-log* safe to run at all, because a decision that
can be found can be reversed. A decision made and not recorded is the same
defect class as a verdict with no evidence behind it. The morning review is
where that record surfaces.

### At fleet scale

The same dial governs a fleet-wide overnight run, and *route-around* gets
structurally stronger as the fleet grows: with many slugs in flight there is
almost always unblocked work somewhere, so parking one slug costs the night far
less than it costs a single-slug run. Blockers centralize onto the one board
(§10) with their resume handles intact, so the morning's first screen is the
list of decisions the operator actually owes — in priority order, with
everything else already done.

## 12. Model families, routing, and the pools

splock is **Anthropic-native at the harness and open at the model call.** That
distinction governs everything in this section.

| Layer | What it is | Routable off Claude? |
|---|---|---|
| **Tier A — model calls inside stages** | planner, coder, reviewer, qa, recon, research, eli5 | **Yes** — this is the achievable, high-value routing |
| **Tier B — the harness itself** | a `/splock:` stage running under a non-Claude CLI | Only by porting the plugin's commands, agents, skills, and hook dialect to that host — explicitly a stretch, and may be deferred indefinitely |

### The family roadmap

Early cuts target three families, all driven through their CLIs: **Claude Code,
Codex (OpenAI), and Antigravity (`agy`, Google).** Later generations extend to
**Grok** and to **an array of on-prem, locally run open-source models.** Each
addition enters under the same rules, no exceptions: subscription or local
transport only, CLI-wrapped, capability-filtered by role.

Local models matter to this vision for a reason beyond cost: **a local model has
no pool at all.** That makes it the natural sink for offloaded work once the
routing machinery exists, and the eventual answer to a fleet that is
pool-limited by construction.

### Routing is a capacity play, not only a quality play

The classic argument for multi-family routing is quality — send each role to the
family that is best at it. splock takes that, and takes something the
subscription constraint (§4.11) makes larger:

**Three families are three subscription pools.** Every unit of work moved off
the dominant family is headroom preserved in that family's weekly allowance —
headroom the operator wants reserved for the work that genuinely needs a
high-reasoning model. Routing a research pass to Codex is not primarily a bet
that Codex researches better; it is a decision to **not spend Claude pool on
prose.**

That reframes the objective. Model routing optimizes for *pool headroom where
reasoning depth is needed*, and quality-fit is how it chooses among the
otherwise-equivalent places to spend.

### How model routing decides

- **Every splock agent carries a default family.** Roles have a shipped
  preferred family; the system is useful with the operator changing nothing
  (§4.12).
- **The operator sets a dominant model and a spread.** Two dials: which family
  is home, and how aggressively splock is allowed to move work off it.
- **Or the operator selects auto.** In auto mode splock balances across the
  families' **current weekly usage**, spending the pools that have room and
  protecting the one it is saving for heavy reasoning.
- **The routing key is role × subject, not role alone.** Both the *agent* and
  the *subject matter* select the model. A family known to be strong in a
  particular discipline is preferred for that discipline — and that preference
  matters most precisely when splock is trying to offload from a preferred
  family, because it decides *where* the offloaded work should land.
- **Live usage rates drive; context does the switching.** The decision inputs
  are the role, the subject, and observed pool draw. This is what makes model
  routing dynamic rather than a static table.
- **Dynamic across spawns, sticky within one.** A route is chosen once per
  role/spawn and held for that workflow. Per-message re-routing is an
  anti-pattern under prompt-cache affinity, and "dynamic" never means
  mid-conversation family hopping.

An illustration of the shape, not a shipped default: research and even planning
run on Codex, `/code` stays on a Claude reasoning model, and the remaining roles
spread across families — so the pool that matters is spent on the work that
matters. The concrete default map is `routing_rules_v1`
(`docs/MULTI_ROUTING_ROADMAP.md`, Phase 4) and is expected to move as real pool
draw gets measured.

### Standing constraints on model routing

- **The verifier is never routed.** Pinning it is the independence invariant
  (§4.2, §7), not a portability gap.
- **Any non-Claude transport wraps that host's CLI, never its metered SDK.** The
  subscription constraint (§4.11) applies to every family, and it is the reason
  some capabilities are simply unavailable — schema-constrained emission is
  present on some subscription CLIs and absent on others, and the router filters
  roles on capability rather than pretending otherwise.
- **Routing stays lightweight and native.** It uses each host's own modern
  surfaces rather than reimplementing them, and there is **no
  provider-abstraction layer for its own sake**. Model pins stay plain,
  documented values. The seam exists to serve routing, not genericism.
- **Every routing decision is forensic.** A decision records the rule that fired,
  the reason, and the concrete resolved model — never an alias. A route nobody
  can reconstruct is the same defect class as a verdict with no receipt (§4.8).

### Related, and deliberately separate: inter-agent communication

The research conclusion stands — **do not build free-form agent-to-agent
messaging.** A "teams" feature is an orchestrator-mediated,
one-writer-per-resource blackboard handoff, which fleet already is. Messages are
the lowest-trust input class and carry their sender's tool scope and permission
denials as provenance; receivers re-verify load-bearing claims rather than
merely confining them.

## 13. Adoption — how splock is used

splock is distributed as a self-hosted Claude Code marketplace containing a
single plugin. **It installs with everything on.**

The stance is opt-out, not opt-in. An adopter gets the full framework from the
install, gets value before configuring anything, and switches *off* what they do
not want. There is no tier to assemble and no graduation path to climb — a
partial splock is a choice the adopter makes later, not a ladder they start at
the bottom of.

| Surface | Default | Switching it off means |
|---|---|---|
| **Enforcement spine** | on | **not offered** — the spine is what splock *is* (§4.1, §6) |
| **Completion gate + verifier pin** | on | **not offered** (§4.2, §7) |
| **Deferred-work routing** — markers, outstanding ledger | on | found problems route only to fix-now or tier-promote; no ledger is kept (§9) |
| **fleet** — per-slug tracking, hub, C&C | on | single-slug lifecycle only: no hub, no board, no spawn (§10) |
| **Model families** | all available | restrict to a single org — Anthropic-only, OpenAI-only, and so on; routing collapses to that family's role coverage (§12) |

**Two surfaces are deliberately not switchable, and they are the same two this
document keeps returning to: the enforcement spine and the verifier pin.** A
framework whose guarantees can be configured away has no guarantees — it has
preferences. Everything above the line is capability, and capability is the
adopter's call; those two are the product.

**The adopter contract is the real product surface.** Read-only install root vs.
persistent data root, resolved through one helper, never a repo-relative guess;
a sealed inventory expressed relative to the adopter's project root; the
adopter's own interpreter and test runner, not the plugin's. Every
foreign-adopter bug so far has been a violation of exactly this contract (`OI-1`,
`OI-3` F1–F8) — which is the evidence for the claim.

**Our own use.** The first foreign adopter (`qum`) is where field lessons come
from; splock itself is the second adopter and the permanent proving ground. The
OSS intent is that the plugin is genuinely usable by someone with none of our
context.

## 14. Threat model — what splock defends against

Stated plainly, because "governed" is meaningless without a named adversary. The
adversary is **not** a malicious operator; it is a capable, well-intentioned,
unreliable agent, plus whatever the agent reads.

Defended:

- an agent that starts coding before there is a plan, or drifts outside its
  scope;
- an agent that declares success without a green run, or makes the run green by
  gutting the test;
- an agent that weakens the guardrails meant to catch it;
- an agent that edits another agent's files, concurrently;
- an agent that quietly abandons a finding it has no room for, or defers one
  without recording what would bring it back (§9);
- an agent that, running unattended, makes a decision that was the operator's
  and does not say so (§11);
- content the agent merely *reads* carrying instructions that contradict its
  contract — the enforcement spine does not consult the conversation;
- a subagent or headless run reaching a human-only privileged path;
- unsafe installs, raw DDL, secret writes, and runaway output.

Not defended, and stated so nobody assumes otherwise:

- a malicious operator, or an operator who disables the spine;
- anything below the OS boundary — that is what the Tier-3 hardening doc is
  for, and on WSL a `NOPASSWD` sudoers silently voids it;
- **the correctness of the tests themselves.** splock enforces that tests ran
  and passed; it never claims they were good tests. This is the most common
  misreading of the completion gate, and it is stated here so the gate is not
  sold as something it is not (§4.14).

## 15. What splock does not do (the drift guards)

Direct consequences of §4:

- It **does not trust prose for enforcement** — ever, anywhere, including its
  own. (§4.1)
- It **does not let an agent self-certify**, and does not expose the verifier
  model as a knob. (§4.2, §7)
- It **does not let an agent edit the plan of record, the state, or the
  plumbing** by hand. (§4.3, §4.4, §4.10)
- It **does not hand-author derived state**, and retires hand-authored surfaces
  on adoption. (§4.5)
- It **does not pass a gate it could not evaluate.** (§4.7)
- It **does not flip a status without leaving an artifact** — including a
  decision made on the operator's behalf overnight. (§4.8, §11)
- It **does not let a finding die quietly**, and does not accept a deferral with
  no reopen condition. (§4.13, §9)
- It **does not let an unattended run relax the gate**, whatever the blocker
  tolerance. (§11)
- It **does not read or require a metered API key** — on any model family.
  (§4.11)
- It **does not require configuration** to be useful. (§4.12)
- It **does not ship a GUI or dashboard** — the console is a downstream consumer
  of splock's JSON and CLI surface, not part of the plugin. (FO-3)
- It **does not integrate with an external issue tracker or system of record.**
  The ledger is in the repo. (§2, §9)
- It **does not support Windows-native shells** in v1; POSIX only.
- It **does not abstract providers** for genericism's sake. (§12)
- It **does not ship host or personal identity** in the public tree. (§4.16)

## 16. Open — not settled by this document

1. **Green-path receipts (#57).** §4.8 states the invariant; the green path does
   not yet honor it. What remains open is scope and cost — how much reviewer
   reasoning a green verdict must persist — not whether the asymmetry is a
   defect.
2. **Fleet-addressed investigation mechanics (§10).** The stance is settled; the
   mechanism is not. Open: what confidence threshold separates "assign" from
   "halt and ask", whether a split qna emits one artifact per slug or one
   artifact with per-slug sections, and whether recon/research/qna can *create*
   a slug directly or must route through tier-promote (§9).
3. **The kickoff question window (§11).** Open: how long the window is and
   whether it is time-bounded or completion-bounded, and what happens to a
   question an agent discovers *after* the window closes under each tolerance
   setting.
4. **Whether escalation triggers are adopter-tunable.** The trigger set is
   closed and CLI-detected by design, but at least one threshold is already an
   env knob. Which trigger parameters an adopter may tune — and which are, like
   the verifier pin, deliberately not offered — is unsettled.
5. **The blocker-tolerance ladder (§11).** Three rungs are named; whether a
   fourth belongs between *route-around* and *decide-and-log* is open.
6. **FO-1 `/splock` privileged plumbing-admin.** Design settled, brief
   build-ready; whether it ships in this horizon is not.
7. **Multi-routing go/no-go.** Tier A phases 1–4 are specified and
   unimplemented. Also open: verifier routing stance, `agy` provenance/billing,
   and whether the subscription-only policy admits any metered escape hatch.
8. **How auto mode observes pool draw.** §12 requires live weekly usage as a
   routing input, and not every host CLI surfaces its own consumption. Whether
   this is read, estimated, or operator-declared is unsettled — and it gates
   auto mode, not model routing as a whole.
9. **When Grok and local open-source models enter**, and whether a local model
   (no pool, no subscription, no metered key) needs any rule beyond §4.11.
10. **Agent teams sequencing** — after Codex spawn support, or never.
11. **OSS posture** — what contribution means, what compatibility promise
    adopters get across plugin versions, and how adopter-found bugs (the `OI-`
    stream) flow back.
12. **Repo history / identity decision (`OI-2`)** — the frozen single-commit
    hygiene tests versus ordinary local development.
13. **Agent surfaces for `wrap` and `close` (§5).** Both ship as CLIs with no
    agent-facing surface, so the two moments that bracket a slug — folding
    findings into the plan record, and closing the slug out — are the two the
    lifecycle does not drive. Open: whether closeout becomes a stage command, a
    subagent, or an automatic consequence of the terminal transition; and
    whether `wrap` should ever be agent-invoked at all, given that it is a trust
    boundary and the agent is the party it is bounding. Recorded as `OI-4`.
14. **What "fleet on by default" costs a single-slug adopter (§13).** fleet is
    opt-in today (`bin/fleet init` writes `_fleet_meta.json`); the §13 stance
    inverts that. Open: whether an adopter with one slug should carry hub and
    per-slug state from install, or whether fleet should self-activate on the
    second slug — on by default, but not before it means anything.

## 17. What is actually built

This document describes intent; **it is not evidence that any part of it
exists.** Check before citing this vision as grounds that a subsystem is
available.

The built-vs-not-built record is kept separately, in repo facts only, updated in
the same commit as the work that changes a row:
**[`docs/IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md)**. That split is
deliberate — this file stays a pure statement of intent, while the anti-drift
check stays current. **Read it before citing any clause here as grounds that a
subsystem is available.**

Two cautions worth carrying inline, because they are the most likely
misreadings of this version. The deferred-work machinery of §9 is **shipped** —
`bin/marker`, `bin/route_issue`, `schemas/marker_v1.schema.json`, the prefix
registry, and the outstanding ledger are all in the tree; what §9 adds is the
doctrine, not the code. The fleet-addressed investigation of §10 and the
overnight mode of §11 are the reverse: an overnight chain driver, pause, resume,
and morning review exist, but the kickoff question window, the blocker-tolerance
dial, and fleet-scale overnight are **stated intent, not shipped behavior**.

Three narrower records feed the status doc and remain authoritative in their own
domains: `docs/FOLLOW_ONS.md` (named deferred scope), `outstanding_issues.md`
(field bugs), and `docs/MULTI_ROUTING_ROADMAP.md` (model routing).
