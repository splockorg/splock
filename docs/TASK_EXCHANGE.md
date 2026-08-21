# Task exchange — cross-operator stage pickup for splock

**Status:** concept / context document. No code, no plan substrate, no slug yet.
**Proposed slug:** `task_exchange`.
**Last updated:** 2026-08-21.
**Diagrams:** [`docs/TASK_EXCHANGE_DIAGRAMS.md`](TASK_EXCHANGE_DIAGRAMS.md) (mermaid).
**Owner decision pending:** whether to mint the slug and run
`/plan task_exchange` scoped by this document.

## What this document is

A hand-authored context doc — the same class as
[`docs/MULTI_ROUTING_ROADMAP.md`](MULTI_ROUTING_ROADMAP.md), **not** a splock
plan substrate. It states the idea, the model, the invariants it must hold, the
substrate it reuses, and — because the feature routes work between separately
billed humans — the compliance boundary it must never cross.

---

## The problem

splock's lifecycle is not uniform in what it costs or what it needs. A `/plan`
Call 2 or a hard `/code` task wants the strongest model at high effort; `/recon`
and `/eli5` do not. Two things break that today:

1. **Entitlement.** A developer on a Pro plan may not have the model a stage
   wants at all. `bin/fleet spawn <slug> --stage code --model claude-fable-5`
   simply is not available to them.
2. **Capacity.** A developer with the entitlement hits the 5-hour or weekly
   window mid-pipeline. `max_concurrent` in `_fleet_meta.json` already exists
   *because* "all children draw ONE subscription pool" (`docs/FLEET.md`). When
   that one pool is dry, the whole board stalls — even though four teammates
   are sitting idle with full pools and the same repo checked out.

Today the only answers are "wait for the window to roll" or "downgrade the
stage to a weaker model", and the second one quietly damages the work.

Meanwhile the team has an obvious latent resource: on a five-person team, at any
given moment most pools are mostly unused, and at least one person usually has
the entitlement the stalled stage needs.

## The thesis

> **Route the task, never the credential.**

A stalled stage is a small, serializable descriptor: a repo, a git ref, a slug,
a stage name, a model requirement, an operator directive. That descriptor is a
few hundred bytes. Publish it to a table the team shares; let each operator's own
machine, running under that operator's own account, decide whether to pick it up;
let the result come back as a git ref.

Nothing about a credential, a token, a session, or a cookie ever moves. Every
model call is still made by an operator's own Claude Code, on that operator's own
machine, against that operator's own subscription, having been accepted by that
operator's own policy. The exchange is a **work queue between peers**, not a
proxy, not a pool, and not a broker of access.

That distinction is the entire feature. It is also — see §Compliance — the
entire difference between a design that is fine and a design that is not.

## Vocabulary

| Term | Meaning |
|---|---|
| **Operator** | A human with their own Claude subscription and their own machine. |
| **Worker** | One operator's splock installation, registered in the exchange. |
| **Offer** | A stage of work published to the exchange, unassigned. |
| **Claim** | The single winning assignment of one offer to one worker. |
| **Lease** | The time-boxed right to execute a claim; expires without heartbeat. |
| **Matcher** | The deterministic cron that turns open offers into claims. |
| **Return** | The git ref + result envelope the worker hands back. |

## The model

Four tables, one append-only log, one cron. The substrate already exists: splock
ships a backend-pluggable DAL (`bin/_intent/db.py`) with SQLite and MySQL
adapters, a SERIALIZABLE + `SELECT ... FOR UPDATE` atomicity contract, and a
read-only MySQL MCP lane gated by `mysql-mcp-guard`. The exchange is a fifth
table family on that same connection, not a new piece of infrastructure.

### `exchange_workers` — who exists and what they can do

Heartbeated by each operator's cron. Carries **capability** (which models this
account can actually spawn, which stages this operator permits, which repos this
machine has) and **capacity** — the `limits[]` rows read from the OAuth usage
endpoint (`percent`, `severity`, `resets_at`, and the scoped model name), with
the `as_of` of that read. Never carries a token: the worker reads its own
credential locally and publishes only the numbers.

### `exchange_offers` — what needs doing

Published by an operator (or automatically by fleet on a `blocked` halt whose
blocker is entitlement or capacity). Carries the routing requirement — `stage`,
`required_model`, `min_effort`, `repo`, `base_ref`, `slug`, the stored spawn
directive — plus `visibility` (which workers may see it) and a deadline.

### `exchange_claims` — who won

One row per offer, `UNIQUE(offer_id)`. This uniqueness constraint *is* the
mutual exclusion: the matcher does not need a lock protocol beyond it. A losing
matcher gets a duplicate-key error and stops, which is the correct outcome.

### `exchange_leases` — is it still alive

`claim_id`, `expires_at`, heartbeat. A worker that dies, sleeps, or loses its
network stops heartbeating; the lease expires; the sweeper returns the offer to
`open` with an incremented `attempt`. No human notices, and no work is lost
because nothing was destructive.

### `exchange_event_log` — what happened

Append-only, same discipline as `intent_event_log` and the per-slug fleet log:
every publish, match, claim, lease-expiry, return, and refusal, with `emitted_by`
and `host`. This is the audit trail that makes the whole thing legible after the
fact, and it is the first thing to reach for when the matcher picks someone
surprising.

## Invariant 1 — zero tokens before assignment

**Nothing in the matching path calls a model.** Publishing an offer is an INSERT.
Heartbeating is an UPDATE. Matching is a SELECT, a sort, and an INSERT. Rejecting
an offer is a WHERE clause. The first token spent anywhere is the winning
worker's `claude -p` for a stage it has already been definitively assigned.

This matters for three reasons: it makes the exchange nearly free to run at any
polling frequency; it means a badly-tuned matcher wastes milliseconds instead of
pool; and it keeps the scheduler outside the model boundary entirely, which is
what lets the compliance argument be simple.

The cost of this invariant is that the matcher can only sort on what a database
row can hold. It cannot "ask" anyone anything. Every input to the decision must
be a fact a cron can write down for free.

## Invariant 2 — the assignment is deterministic

Given the same table snapshot, every matcher on every machine computes the same
winner. Not "usually the same" — the same. That is what makes it debuggable, what
makes double-assignment structurally impossible rather than merely unlikely, and
what lets any operator explain to any other operator why they got a task.

The sort key, first to last, all integers or fixed strings, no floats, no clock
reads inside the comparison:

1. **Eligibility is a filter, not a score.** A worker that lacks
   `required_model`, lacks the repo, has the stage disabled in local policy, is
   outside the offer's `visibility`, or has a stale heartbeat is removed. It
   cannot be outweighed by a good score elsewhere.
2. **Headroom** (descending) — the vendor-computed `percent` for the binding
   limit, inverted. The narrowest applicable row wins: a `weekly_scoped` row for
   the offer's `required_model` if one exists, else `weekly_all`, else
   `session`. No local quantisation — the number is an integer the vendor
   computed, identical for every matcher that reads the same row.
3. **Fairness debt** (descending) — offers executed *for others* minus offers
   executed *by others for you*, over a rolling window. This is what stops the
   one teammate on the biggest plan from silently becoming the team's build
   server, and it is a compliance control as much as a courtesy one.
4. **Concurrency headroom** (descending) — declared `max_concurrent` minus live
   claims. A worker at its own configured ceiling drops out.
5. **Deterministic tiebreak** — `blake2b(offer_id || worker_id)` ascending. Fixed
   hash, no salt, no randomness, no wall clock. Two workers identical on every
   prior key resolve identically on every machine, forever.

Ties are therefore impossible, and a `RANDOM()`, a `NOW()`, or a float anywhere
in that list is a bug, not a refinement.

The write is a single guarded statement — `INSERT INTO exchange_claims` on the
unique key, or `UPDATE exchange_offers SET state='claimed' ... WHERE
state='open'` with an affected-rows check. Exactly one succeeds. The determinism
above is what makes the *outcome* explainable; the unique key is what makes it
*correct* even if determinism is later broken by a bug.

## Invariant 3 — headroom is read locally, never guessed and never proxied

Anthropic exposes an OAuth-authenticated quota endpoint that costs **zero model
tokens** — it reports quota, it does not talk to a model:

```
GET https://api.anthropic.com/api/oauth/usage
Authorization: Bearer <accessToken from ~/.claude/.credentials.json>
anthropic-beta: oauth-2025-04-20
User-Agent: claude-code/<installed CLI version>
```

Verified live 2026-08-21. The payload's `limits[]` array is exactly the routing
input this design needs, already structured:

```json
{"kind": "session",       "percent": 9,   "severity": "normal",   "resets_at": "…"}
{"kind": "weekly_all",    "percent": 86,  "severity": "warning",  "resets_at": "…"}
{"kind": "weekly_scoped", "percent": 100, "severity": "critical", "resets_at": "…",
 "scope": {"model": {"display_name": "Fable"}}}
```

Three consequences, all of which make the design simpler than a headroom
estimate would have:

1. **Entitlement and capacity are one read, not two.** A `weekly_scoped` row
   names the model and its utilisation together — "does this account have Fable"
   and "how much Fable is left" arrive in the same field. The eligibility filter
   and the capacity sort read the same source.
2. **The sort key needs no quantisation.** `percent` is already an integer and
   `severity` is already a three-level band, so the matcher sorts on a value the
   vendor computed rather than one each worker guessed. Determinism gets *easier*
   — there is no estimator whose drift could make two workers disagree.
3. **`resets_at` makes waiting a comparable option.** The matcher can weigh
   "A recovers in 20 minutes" against "hand it to B now", which a
   utilisation-only view cannot express.

**Each worker reads its own token, locally, and publishes only the numbers.**
The `accessToken` is read from that machine's own `~/.claude/.credentials.json`
and sent only to the endpoint that issued it. It never enters an offer, a claim,
or the exchange tables — what lands in `exchange_workers` is
`{percent, severity, resets_at, model}`, which is not a credential. This is the
same boundary Invariant 1 and the compliance section draw, applied to the
telemetry path: **numbers cross the machine boundary, credentials never do.**

**The endpoint is unofficial and undocumented**, and the design must treat it as
such. It is absent from Anthropic's published API reference; the payload shape
drifts (the live response carries a dozen codename keys — `nimbus_quill`,
`tangelo`, `iguana_necktie`, `cinder_cove`, `amber_ladder` — most of them null,
which is why a consumer must walk `limits[]` rather than hardcode key names); it
rate-limits with 429s of its own; and it buckets unrecognised clients more
aggressively, so a caller must identify honestly as the installed CLI version.
Requirements that follow:

- **Poll it on a schedule, not per decision.** The heartbeat cron reads it once
  per interval and writes the result; the matcher reads the table, never the
  endpoint. One read per worker per interval, regardless of how many offers are
  in flight.
- **Degrade, do not fail.** A 429, a 401, a schema change, or a missing
  credentials file must leave the worker eligible-but-stale rather than crash
  the cron — the row keeps its `as_of`, and a heartbeat too old to trust drops
  the worker from eligibility exactly as a dead worker does.
- **Keep the local-ledger estimator as the fallback path**, not the primary one.
  `_fleet_runs.jsonl` and observed rate-limit halts still work when the endpoint
  does not, at lower fidelity.

Prior versions of this document asserted that no scriptable quota read existed
and designed an estimator around that. That was wrong — the CLI has no `usage`
subcommand, which is true and was over-generalised into a claim about the whole
platform. The estimator survives only as the degradation path above.

## Invariant 4 — the transport is git, never a working tree

An offer names `repo` + `base_ref`. A worker that accepts it fetches that ref
into its own checkout, runs the stage, and returns a **branch push plus a result
envelope** — never a diff blob in a database row, never a file copy, never a
`.env`. Workers that cannot resolve the repo are ineligible at filter time, which
means the exchange never has to think about code transport at all: git already
solved it, and the team's existing access controls on the repo are the same
access controls that govern who can pick up work.

Corollary: **secrets never enter an offer.** The offer carries a directive
written for a teammate, not an environment. A worker's own `.env`, its own MCP
credentials, and its own sealed paths stay entirely local. `mysql-mcp-guard` and
the sealed-path deny list keep applying to the borrowed stage exactly as they
apply to a local one.

## Invariant 5 — an offer is untrusted input

An offer is text authored on someone else's machine that arrives in this
operator's agent context. That is the confused-deputy shape the agent-teams
research already flagged (`docs/plans/agent_teams/agent_teams_research.md`), and
its conclusion carries over unchanged: the offer body must be wrapped at the
**lowest trust level** (`WrapKind` `agent-message` class), it must never be able
to grant a permission, and load-bearing claims inside it must be re-verified
locally rather than merely confined.

The exchange makes this sharper than the in-fleet case, because the author is a
different *person*, not a different process owned by the same person. Concretely:

- the offer envelope carries the publishing operator's identity, tool scope, and
  `permission_denials` as provenance;
- an accepting worker applies **its own** stage policy, its own deny-list, and
  its own permission mode — the offer cannot request `--dangerously-skip-permissions`,
  cannot name a model the local policy forbids, and cannot widen `allowed_tools`;
- the returned branch is reviewed by the *publishing* operator before merge. A
  borrowed stage produces a proposal, not a merge.

## How it fits what already exists

| Existing piece | Role in the exchange |
|---|---|
| `bin/_fleet/spawn.py` | The executor. A claimed offer becomes a normal headless `claude -p "/splock:<stage> <slug>"` child on the winning worker. No new transport. |
| `bin/_fleet/runs.py` / `board.py` | The fallback headroom signal when the usage endpoint is unavailable, and the natural place to render borrowed/lent work. |
| `_fleet_meta.json` profiles + `max_concurrent` | Already the local capacity + policy surface; the exchange reads it rather than inventing a second one. |
| `bin/_intent/db.py` | The DAL, the SQLite/MySQL split, and the atomicity contract. The exchange is new tables, not a new database layer. |
| `mysql-mcp-guard` | Already gates the MySQL MCP lane read-only; the exchange's writes go through `bin/` code paths, not the agent's MCP lane. |
| `unspawnable_stages` / `FLEET:ATTENDED` | Already encodes "this stage needs a human at the keyboard". Attended stages must be un-offerable — the deny-list is reused verbatim. |
| `docs/MULTI_ROUTING_ROADMAP.md` | The sibling answer to the same problem. Multi-routing sends the stage to a *different vendor*; the exchange sends it to a *different teammate*. They compose: a worker may itself be routing to Codex or Antigravity. |

The exchange is deliberately **the smaller of the two ideas**. Multi-routing
needs transports, schema sanitisation, and capability filters per family. The
exchange needs four tables and a cron, and reuses splock's existing spawner
untouched.

## Failure modes worth designing for up front

- **Nobody is eligible.** The offer ages out to `expired` and the publishing
  operator is told, with the filter that eliminated everyone. Silence would be
  the worst outcome; a stalled board that *looks* like it is being handled is
  worse than one that visibly is not.
- **The winner dies mid-stage.** Lease expiry requeues with `attempt+1`. After
  `max_attempts` the offer goes `failed`, not around again forever.
- **The winner is slower than waiting.** If the publisher's own window rolls
  before the borrowed stage returns, the borrowed work was wasted pool — someone
  else's. The publisher should be able to `withdraw` an offer, and a claimed
  offer should be `cancel`-able with the worker honouring it at the next
  heartbeat.
- **Clock skew between operators.** All timestamps are DB-server time, taken from
  a single `NOW()` per matcher transaction. Machine clocks are never compared.
- **The headroom read is stale.** Utilisation moves between heartbeats, so a
  worker can accept and then immediately hit its limit. It returns
  `rate_limited`, which forces a fresh endpoint read and requeues the offer. The
  system self-corrects without a human tuning anything — and the shorter the
  heartbeat interval, the narrower this window, which is what makes poll cadence
  a real design parameter rather than a preference.
- **The usage endpoint changes or goes away.** It is unofficial. The worker falls
  back to the local-ledger estimate, marks its headroom `degraded`, and the
  matcher weights it below any worker with a live read.
- **One person becomes the team's server.** This is the fairness-debt key, and it
  is a hard cap, not just a sort term: a per-operator daily ceiling on borrowed
  execution, refused at filter time.

## Compliance — where the redline is

This feature moves work between separately billed accounts, so the boundary is
worth stating precisely rather than assuming. The relevant primary sources,
retrieved 2026-08-21:

- **Consumer Terms §2 (Account creation and access):** *"You may not share your
  Account login information, Anthropic API key, or Account credentials with
  anyone else. You also may not make your Account available to anyone else. You
  are responsible for all activity occurring under your Account."*
- **Consumer Terms §3 (Use of our Services)** prohibits using the Services *"to
  develop any products or services that compete with our Services ... or resell
  the Services"*, and *"except when you are accessing our Services via an
  Anthropic API Key or where we otherwise explicitly permit it, to access the
  Services through automated or non-human means, whether through a bot, script,
  or otherwise."*
- **Claude Code Legal and compliance — Authentication and credential use:**
  *"OAuth authentication is intended exclusively for purchasers of Claude Free,
  Pro, Max, Team, and Enterprise subscription plans and is designed to support
  ordinary use of Claude Code and other native Anthropic applications."* and
  *"Anthropic does not permit third-party developers to offer Claude.ai login or
  to route requests through Free, Pro, or Max plan credentials on behalf of their
  users."*
- **Same page — Acceptable use:** *"Advertised usage limits for Pro and Max plans
  assume ordinary, individual usage of Claude Code and the Agent SDK."*
- **Agent SDK on a Claude plan (support article, 2026-06-16):** *"Credits belong
  to individual accounts. They can't be shared or pooled across teammates."* and
  *"The Agent SDK monthly credit is sized for individual experimentation and
  automation. Teams running shared production automation should use Claude
  Platform with an API key for predictable pay-as-you-go billing."*
- **Usage Policy (AUP), Do Not Abuse our Platform:** prohibits circumventing
  bans via other accounts and *"coordinat[ing] malicious activity across multiple
  accounts to avoid detection or circumvent product guardrails."*

### Why the design as described is on the right side

The automation clause is the one that looks scariest and is actually the least
problematic: headless `claude -p`, the Claude Code GitHub Action, and cron-driven
Agent SDK use are named by Anthropic's own documentation as supported uses of a
subscription. Scripted Claude Code *is* the explicitly-permitted case. splock
already lives here — fleet's whole C&C layer is `claude -p` subprocesses on the
operator's own OAuth, and `ANTHROPIC_API_KEY` is deliberately never read.

The clauses that actually bind are the credential ones, and the design answers
them structurally rather than by policy:

| Rule | How the design satisfies it |
|---|---|
| No sharing credentials | No credential field exists in any table. There is nowhere to put one. |
| No sharing credentials — telemetry path | Each worker reads its own OAuth token from its own machine and sends it only to the endpoint that issued it. What crosses the machine boundary is `{percent, severity, resets_at, model}` — numbers, not a credential. |
| No making your Account available to others | Each operator's Claude Code runs only on their own machine, under their own login, executing work their own policy accepted. |
| No routing requests through Pro/Max credentials on behalf of users | There is no proxy and no central executor. The central component is a table of text; it never holds a token and never makes a model call. |
| No reselling | No money, no capacity market, no external users. |
| Ordinary, individual usage | Each operator's usage stays their own work, capped by their own ceiling and rate-limited by fairness debt. |

### The redlines — cross these and it is a violation

These are hard, in descending order of obviousness:

1. **Any credential in transit or at rest in the exchange.** An OAuth token, a
   `CLAUDE_CODE_OAUTH_TOKEN`, a `setup-token` value, a session cookie, or a
   keychain export placed in a table, an offer, an env var shipped to a peer, or
   a shared runner image. This is the explicit §2 prohibition and there is no
   reading under which it is acceptable. It is also completely unnecessary: the
   whole point of routing the task is that the credential does not have to move.
2. **A central executor.** A server, container, or "runner" that holds one or
   more subscription logins and runs stages for whoever submits them. That is
   verbatim *"rout[ing] requests through Free, Pro, or Max plan credentials on
   behalf of their users"*. The distinction from the compliant design is not
   cosmetic: compliant = N machines, N accounts, N humans, each executing their
   own accepted work; violating = 1 machine executing everyone's work under
   borrowed logins.
3. **Charging for capacity, in money or in kind, outside the team.** A public
   exchange where strangers trade spare pool is a resale of the Services even
   with no cash involved, and the "no pooling of credits across teammates" line
   makes clear that pooled entitlement is the thing being prevented, not just
   pooled billing.
4. **Multiple accounts to multiply one person's capacity.** Registering several
   subscriptions to one human as separate workers, or spinning up accounts to
   act as a worker fleet. That is capacity circumvention, and coordinating it
   across accounts is named in the AUP.
5. **A machine with no human behind it.** A worker registered to an account whose
   owner is not the person who could, in principle, be at that keyboard. A team
   "spare laptop" logged into someone's account and left to grind is redline 2
   wearing a different hat.

### The amber zone — where judgement is required

Redline 6 has no bright line, and pretending otherwise would be dishonest:
**continuous, unattended, high-volume borrowing.** Nothing in the letter of the
terms forbids a teammate's machine executing a teammate's stage. But *"advertised
usage limits ... assume ordinary, individual usage"* is a statement about
expected shape, and a team that runs the exchange flat-out around the clock has,
in substance, pooled five subscriptions into one shared build capacity — which
is the outcome the credit-pooling rule exists to prevent, arrived at from a
different direction.

Design controls that keep the feature well inside the amber zone, and which
should be defaults rather than options:

- **Per-operator opt-in, per stage class.** Off by default; nothing is borrowable
  until a human enables it on that machine.
- **A daily ceiling on borrowed execution** per worker, enforced at filter time.
- **Fairness debt as a first-class sort key**, so lending is reciprocal by
  construction rather than by goodwill.
- **Attended stages are never offerable** — the existing `unspawnable_stages`
  deny-list already draws this line.
- **Visible ledger.** Every borrowed execution is a row an operator can see on
  their own board. Nobody should discover after the fact that their pool paid
  for someone else's week.
- **An API-key lane for genuinely shared automation.** When a team wants
  always-on shared capacity, Anthropic's own guidance names the answer: *"Teams
  running shared production automation should use Claude Platform with an API
  key."* The exchange should support a worker whose transport is an org-owned
  API key, and that worker is the correct home for anything that starts to look
  like infrastructure. Note this is a deliberate exception to splock's
  subscription-only policy and needs an explicit operator decision — see
  §Open decisions.

**Recommended posture:** ship it as a *team* feature for a small group inside one
organisation, where every worker is a per-seat licensee of the same team, opt-in
is explicit, borrowing is capped and reciprocal, and the ledger is visible. Do
not ship it as a public or cross-organisation exchange; that version is a
capacity market, and a capacity market is the thing the terms are written to
prevent. If usage genuinely outgrows the caps, that is the signal to move the
shared portion to an API key, not to raise the caps.

None of the above is legal advice, and Anthropic *"reserves the right to take
measures to enforce these restrictions and may do so without prior notice."* If
the team's intended volume sits anywhere near the amber zone, the cheap move is
to ask Anthropic directly — the Claude Code legal page links a sales contact for
exactly this question.

## Open decisions to resolve in `/plan`

1. **How much weight can an unofficial endpoint carry?** Headroom is readable
   (Invariant 3), so the open question is dependability, not feasibility: how
   often does the payload shape drift, what is the endpoint's own rate limit,
   and does identifying as the installed CLI stay acceptable? Decide how much of
   the matcher may depend on it and what the degraded mode routes on.
2. **Team-only, or org-federated?** Recommend team-only for v1 — it keeps the
   compliance story simple and matches the motivating five-person case.
3. **Does the subscription-only policy admit the API-key worker?** splock forbids
   metered keys today (`bin/_fleet/spawn.py`, `_force_subscription_auth`). The
   compliant answer to "we want always-on shared capacity" is an API key, so
   either the policy gains a documented exception for exchange workers or the
   feature stays permanently capped.
4. **SQLite or MySQL for v1?** The DAL supports both, but a cross-machine
   exchange needs a shared endpoint; SQLite over a network filesystem is not it.
   Recommend MySQL-only for the exchange tables, with the local worker state
   staying wherever the intent registry already is.
5. **Who reviews the returned branch?** Recommend: always the publisher, never
   auto-merge. A borrowed stage produces a proposal.
6. **Does an offer carry the plan substrate, or just the slug?** Carrying the
   slug keeps offers tiny and forces the worker to fetch from git, which is the
   right coupling; it also means a worker cannot run a stage for a repo it has
   no access to, which is a feature.

## Next step

Mint the slug and run `/plan task_exchange` scoped by this document, with
Open decision 1 as the Phase 0 spike — a dependability soak on the usage
endpoint rather than a feasibility question. If it proves too unstable to sort
on, the plan falls back to the local-ledger estimate and stays worth building.

## Internal references

- [`docs/TASK_EXCHANGE_DIAGRAMS.md`](TASK_EXCHANGE_DIAGRAMS.md) — the mermaid
  topology, ER model, lifecycle, sequence, and matcher.
- [`docs/FLEET.md`](FLEET.md) — the spawn/state substrate this extends.
- [`docs/MULTI_ROUTING_ROADMAP.md`](MULTI_ROUTING_ROADMAP.md) — the sibling
  answer: route across vendors instead of across teammates.
- [`docs/plans/agent_teams/agent_teams_research.md`](plans/agent_teams/agent_teams_research.md)
  — the trust-boundary conclusions Invariant 5 inherits.
- `bin/_intent/db.py` — the DAL, backend split, and atomicity contract.

## External sources

- [Anthropic Consumer Terms of Service](https://www.anthropic.com/legal/consumer-terms)
- [Anthropic Usage Policy](https://www.anthropic.com/legal/aup)
- [Claude Code — Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)
- [Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
- [Use Claude Code with your Pro or Max plan](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan)
