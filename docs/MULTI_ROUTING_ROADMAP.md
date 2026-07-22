# Multi-routing roadmap — splock on Claude + OpenAI + Gemini

**Status:** research + verification complete; implementation not started (0 lines).
**Last updated:** 2026-07-22.
**Owner decision pending:** whether to proceed to `/plan portability_scaffolding`
scoped by this roadmap.

## What this document is

A single map that consolidates the multi-routing investigation so each step's
"what's next" is explicit. It ties together three strands:

1. **Portability** — running splock's work on non-Claude model families
   (`docs/PORTABILITY_REVIEW.md`, `docs/plans/portability_scaffolding/*`).
2. **Verified host capabilities** — what Codex CLI and Antigravity CLI (`agy`)
   can actually do, checked against primary sources *and the binaries installed
   on this machine* (2026-07-22).
3. **Agent teams** — inter-agent communication, a *separate* track with its own
   conclusion (`docs/plans/agent_teams/agent_teams_research.md`).

This is a hand-authored roadmap, **not** a splock plan substrate. The intended
next step is to feed it into `/plan portability_scaffolding`.

---

## The vision (bottom line)

**splock runs on all three AIs, routing each role to the best-fit family.** The
unit of routing is the **role/stage** (planner, coder, reviewer, verifier,
recon, qa) — *not* the individual task. Routing happens once per role/spawn and
is held **sticky** for the workflow (prompt-cache affinity makes per-message
re-routing an anti-pattern) [27]. This is exactly the `StaticRouter → RuleRouter`
design already selected in the portability research [27].

Bonus property: three families = **three subscription pools** (Claude sub +
ChatGPT Plus + Gemini/Antigravity), spreading the concurrency load fleet already
manages across three billing pools instead of one.

---

## Two layers — the distinction that governs everything

| Layer | What it is | Can it route to OpenAI/Gemini? |
|---|---|---|
| **Tier A — model calls inside stages** | planner Call 1/2, QA, reviewer, verifier, SDK-spawned coder | **Yes** — this is the achievable, high-value multi-routing |
| **Tier B — the harness itself** | you typing `/plan`; fleet spawning children that run `/splock:code` | Only by **porting the plugin** (commands/agents/skills/hooks) to each host's format — the big lift |

The reason Tier B is hard: splock's stages **are** Claude Code artifacts. Fleet
spawns `claude -p "/splock:{stage} {slug}"` (`bin/_fleet/spawn.py`) and it works
only because the child is another Claude Code with the splock plugin loaded. A
`codex exec` or `agy -p` child has no idea what `/splock:code` means. De-hardcoding
the binary is easy; making a non-Claude child *do a splock stage* is the project.

**Most of the "delegate to the best AI" value lives in Tier A.** Tier B is
optional and can come much later (or never).

---

## Verified host capability matrix (ground truth, 2026-07-22)

| Capability | Claude (`claude`) | OpenAI (`codex`) | Gemini/Antigravity (`agy`) |
|---|---|---|---|
| Headless one-shot | `claude -p` ✅ | `codex exec` ✅ | `agy -p`/`--print` ✅ (v1.1.2) |
| Non-interactive resume | `--resume` ✅ | `exec resume --last` ✅ | `--conversation <id>` / `--continue` ✅ |
| Subscription auth (no metered key) | ✅ (this is splock's policy) | ✅ `~/.codex/auth.json` (ChatGPT Plus) | ✅ via `~/.gemini/` (Antigravity) |
| Structured output / JSON schema | ✅ | ✅ `--output-schema` (caveats) | ❌ no schema flag — fixed envelope only |
| Hooks / deny-allow dialect | ✅ (splock spine) | ✅ 10 events, exit-2 + JSON | ✅ `~/.gemini/hooks.json` |
| MCP client | ✅ | ✅ (+ server mode) | ✅ (client) |
| On this machine right now | installed | **auth present, binary NOT installed** | **installed + configured** |

Key caveats:
- **Codex `--output-schema`** is silently ignored when MCP/tools are active, and
  is incompatible with `exec resume`. Subagents return text only. [12][14]
- **Gemini CLI** (the `gemini` binary, distinct from `agy`) had its **consumer
  login removed 2026-06-18** — a Google AI Pro sub no longer drives it; only
  metered API key / Vertex [17][18]. `agy` (Antigravity) is Google's migration
  target and is the viable Gemini-family CLI, but with **no structured-output
  schema** [19] and unconfirmed official-distribution/billing model.
- **`agy` headless mode is verified from the installed binary** (`agy --help`,
  v1.1.2, 2026-07-22) — this overrides the earlier web-research claim that
  Antigravity had no scriptable surface. **Execution smoke-tested 2026-07-22:**
  `agy -p "…" ` returned a completion, exit 0 — auth is live. Billing is not
  surfaced in CLI output (dashboard/log check still open). [20]

---

## The role → family routing map (the target `routing_rules_v1`)

| Role | Preferred family | Rationale | Freely routed? |
|---|---|---|---|
| **recon / research** | Gemini (`agy`) | prose + search, no schema needed — cost slot | ✅ |
| **coder** | Codex or Gemini | bulk throughput; pick on cost/limits | ✅ |
| **planner (Call 2 schema emit)** | Claude or Codex | needs structured output — **Gemini excluded** | ✅ (not Gemini) |
| **qa / reviewer** | Claude or Codex | adversarial reasoning + structured verdicts | ✅ |
| **verifier** | **Claude Haiku — PINNED** | independence invariant; the gate the coder must not be able to relax | ❌ deliberately not routed |

The verifier stays pinned by design — that is a *feature* of splock's
"coder can't self-certify" premise, not a portability gap. Cross-family
verification (e.g. a Codex coder checked by a Claude verifier) is arguably the
strongest independence but is an unmeasured regime; treat it as an open
governance decision, not a default.

---

## Reconciliations vs. prior design docs (verified 2026-07-22)

This roadmap post-dates `PORTABILITY_REVIEW.md` / recon / qna (2026-07-15/16) by
a week, and the repo moved under them (fleet + eli5 landed 07-18). Where they
conflict, the authority is: **external CLI/provider facts → this session's
ground-truth + primary sources** (the docs are self-flagged docs-derived, recon
G1); **splock internal state → the live repo code** (docs are stale where code
moved); **architecture/design → the prior docs stand** (validated, not
overturned). Five corrections the plan must carry:

1. **Gemini cannot do constrained emission on the subscription CLI.**
   `PORTABILITY_REVIEW.md:79` and the recon's `sanitize_schema` assume an
   `AntigravityTransport` serves schema-bound roles via Gemini `responseSchema`.
   `responseSchema` lives in the **metered Gemini API**, not `agy`/Gemini-CLI —
   the CLIs have no schema flag [19]. Under splock's subscription-only policy the
   planner (Call 2) therefore **cannot** route to Gemini. The role map above is
   the corrected design; the recon's Gemini-schema assumption is superseded.
2. **Every non-Claude transport wraps the CLI, never the metered SDK.** The recon
   treats "SDK or `codex exec`/CLI" as interchangeable, but `_force_subscription_
   auth` strips metered keys and the Antigravity SDK is API-key-only. So Tier-A
   transports MUST wrap `codex exec` / `agy -p` to keep subscription billing —
   the same hard constraint `bin/_fleet/spawn.py:104` already encodes. This is a
   requirement in the plan, not an option.
3. **fleet is a fourth, un-audited transport.** The recon's coupling audit
   (07-15) predates fleet (07-18) and lists only three transport seams; fleet's
   `build_child_argv` (`["claude","-p",…]`, `spawn.py:104`) is a fourth. Phase 1
   seam-hardening must add it to the inventory even though the fleet-spawn *port*
   itself is Phase 5.
4. **Roster is already `schema_version: 3` (eli5) → routing lands as v4, and the
   role set is 10, not 5.** The qna proposed putting the `routing` mapping in
   roster v3, but v3 was spent on `eli5` (07-18); it must be re-cut as **v4**.
   The recon's `ModelRole` enum has 5 members (planner/qa/coder/reviewer/
   verifier) but the live role set is 9 subagents + `eli5` (a 10th, model-pinned
   `DEFAULT_ELI5_MODEL`) — routing's role vocabulary must cover all of them.
5. **Phase-number mapping** (the prior docs and this roadmap both say "Phase 0"):
   roadmap **P0** (prove transports — new) → **P1** = recon's "Phase 0" seam
   hardening → **P2** = review's "Phase 1" Codex → **P3** = review's "Phase 2"
   Antigravity.

## Phased roadmap

Each phase lists **Goal → Steps → Exit criteria → Next**. Phases 0–4 deliver the
Tier-A multi-routing vision. Phase 5 (Tier B) is optional/stretch. See the
phase-number mapping above — this roadmap's P1 is the prior docs' "Phase 0."

### Phase 0 — Prove the transports (cheap; no splock code changes)
- **Goal:** empirically confirm what each installed CLI can actually do for
  splock's roles, before committing to build.
- **Steps:**
  1. Install the `codex` binary (auth is already present) and smoke-test
     `codex exec` end-to-end.
  2. Smoke-test `agy -p "reply READY"` to confirm the Antigravity auth executes.
  3. **The decisive experiment:** run `codex exec --output-schema <planner
     schema>` against a real splock planner-style prompt — does an OpenAI model
     emit splock's schema-valid substrate?
  4. Confirm (expected) that `agy` cannot do schema-constrained output → fixes
     Gemini's role scope to prose-heavy roles.
- **Exit criteria:** a table of which roles each family can serve, backed by real
  runs (not docs).
- **Next:** if Codex emits valid schema → Phase 1 with confidence in the planner
  route; if not → planner stays Claude-only and Codex is a coder/reviewer
  transport.
- **Results (2026-07-22) — PASSED:**
  - `agy -p` executes (exit 0) on the installed Antigravity auth.
  - `codex` CLI v0.145.0 installed from GitHub release (no npm/cargo here —
    prebuilt `x86_64-unknown-linux-musl` binary to `~/.local/bin`).
  - Codex runs on the **ChatGPT subscription** (`auth.json` `auth_mode: chatgpt`,
    OAuth tokens, **no API key**) — model `gpt-5.6-sol`, `approval: never`,
    `sandbox: read-only`. Subscription-billed structured output confirmed.
  - **The decisive experiment PASSED end-to-end:** raw `plan_v1.schema.json` was
    rejected by OpenAI's strict dialect (`'required' … must include every key in
    properties. Missing 'non_goals'`); after the sanitize transform, GPT-5.6
    emitted a plan substrate that **validates against the *original* strict
    `plan_v1.schema.json`** (pattern slug, `phase`/`tier`/`kind` enums, `const`
    version — all correct). → **Codex can serve the planner and other schema-bound
    roles.**
  - **Proven `sanitize_schema` transform** (the seam CodexTransport implements):
    strip `$schema`/`$id`/`$comment`; recursively set `required` = all
    properties + `additionalProperties:false`; `const`→`enum:[x]`; drop
    `minLength`/`maxLength`/`pattern`/`minItems`/`maxItems`/`format`; **keep
    `enum`**. Then validate the *output* against the original strict schema.
  - **Operational gotchas for CodexTransport:** `codex exec` blocks unless given
    `< /dev/null` (it reads stdin); use absolute `--output-schema`/`-o` paths and
    `-C <dir>` + `--skip-git-repo-check`. The prior caveats still hold —
    `--output-schema` is incompatible with `exec resume` and ignored when MCP is
    active, so the planner call must be a fresh, MCP-free exec.

### Phase 1 — Seam hardening (`bin/_host/`, zero behavior change)
- **Goal:** build the host-adapter interface designed in the recon, with Claude
  as the only implementation, so nothing changes yet.
- **Steps:**
  1. Create `bin/_host/` (events, shim, transport, catalog) — stdlib-only.
  2. Family-keyed transport registry + `StaticRouter` (everything → `claude`).
  3. Thread `RouteQuery`/`RouteDecision` through the three Tier-A DI seams
     (`two_call.py`, `_qa/invoke.py`, retry-loop `iteration_loop.py`), defaulting
     to `StaticRouter`. **Add fleet's `build_child_argv` (`spawn.py:104`) to the
     transport inventory** — it is a fourth `["claude","-p"]` seam the original
     audit missed (reconciliation #3); its port is Phase 5, but it must be named
     here so seam-hardening is complete.
  4. `bin/hook-entry` dispatcher + conformance suite (deny-parity on Claude).
- **Exit criteria:** CI green, **zero behavior change on Claude**, routing seam
  exists but always picks `claude`.
- **Next:** wire the first non-Claude transport (Phase 2).

### Phase 2 — Codex as a model transport (Tier A)
- **Goal:** a real splock run where an OpenAI model does part of the work.
- **Steps:**
  1. Implement `ClaudeTransport` + `CodexTransport` against the `ModelTransport`
     ABC (`complete` / `spawn_agent` / `sanitize_schema`), wrapping **`codex exec`
     (the CLI, not the metered SDK — reconciliation #2)** for subscription auth.
  2. Add capability tags so the router filters on structured-output support.
  3. Route the cost/throughput roles (coder, recon) to Codex via a minimal
     `RuleRouter`; keep planner/qa/reviewer/verifier on Claude.
- **Exit criteria:** a splock task where the **coder is GPT and the verifier is
  Claude Haiku** — cross-family, gate intact.
- **Next:** add the Gemini transport (Phase 3) or expand Codex's role coverage.

### Phase 3 — Gemini/Antigravity transport (Tier A, constrained)
- **Goal:** three-family routing for Tier-A roles.
- **Steps:**
  1. Implement `AntigravityTransport` wrapping **`agy -p` / `--conversation` (the
     CLI, not the API-key SDK — reconciliation #2)**; execution already
     smoke-tested (2026-07-22).
  2. Confirm `agy`'s auth/billing model fits splock's subscription-only policy
     (the one open item — CLI output does not surface cost).
  3. Route prose-heavy roles (recon/research) to Gemini; **exclude planner**
     (no CLI schema support — reconciliation #1). Capability filter enforces this
     automatically via `capabilities()` minus `structured-output`.
- **Exit criteria:** a splock run touching all three families, each on a role it
  fits.
- **Next:** harden the routing rules into config (Phase 4).

### Phase 4 — `RuleRouter` + `routing_rules_v1` config
- **Goal:** operator-tunable routing across the three families.
- **Steps:**
  1. Ship `routing_rules_v1.schema.json` carrying the role→family map above,
     with `as_of` dating on every rule and **no numeric prices in rules**.
  2. Implement `RuleRouter` (first-match-wins, always-valid `static-default`).
  3. Forensics: `RouteDecision.rule_id` + `reason`, `resolved_model` (concrete
     ID, never alias), observed cost for drift detection.
- **Exit criteria:** changing routing = editing config, no code changes; the map
  in this doc is the shipped default.
- **Next:** stop here (Tier A complete) or attempt Tier B (Phase 5).

### Phase 5 — Tier B harness port (optional / stretch)
- **Goal:** let fleet spawn heterogeneous *harness* children — a `codex` or `agy`
  child that runs a full splock stage headlessly.
- **Steps:**
  1. Parameterize `bin/_fleet/spawn.py`'s hardcoded `["claude","-p",...]` argv by
     host family.
  2. Port the plugin's commands/agents/skills/hooks to Codex's TOML-agent format
     and Antigravity's format (or emit host-native self-contained prompts instead
     of `/splock:` slash commands).
  3. Port the enforcement spine (sealed-path deny, wrap boundary) via the
     `HostHookShim` to each host's hook dialect.
- **Exit criteria:** fleet spawns a Codex or agy child that completes a splock
  stage end-to-end with the safety spine enforced.
- **Next:** none — this is the terminal ambition. May be deferred indefinitely if
  Tier-A routing delivers enough value.

---

## Related track: agent teams (separate, deferred)

Inter-agent communication is its own investigation
(`docs/plans/agent_teams/agent_teams_research.md`). Its conclusion is
independent of routing: **do not build free-form agent-to-agent messaging.** The
evidence — MAST: ~37% of multi-agent failures are inter-agent comms [3];
Cognition's retreat from peer messaging for coding [4][5]; Anthropic's own
coding carve-out and subagents that never message each other [6][7]; no shipping
host exposes peer messaging (Claude Code's Agent Teams is the closest, and its
mailbox has a *weaker* race story than fleet) [21] — says a "teams" feature
should be an orchestrator-mediated, shared-state, one-writer-per-resource
**blackboard handoff** [8][11], which fleet already is, hardened with a new
lowest-trust `WrapKind` (`agent-message`) [23][24][25] and **between-turn**
delivery (no host supports mid-turn injection) [22]. Sequence it *after* Codex
spawn support; it is not a prerequisite for multi-routing.

The protocol survey reinforces "build in-process, adopt no standard": A2A and
MCP-as-messaging solve a cross-organizational trust boundary a local fleet does
not have, and MCP's peer-messaging substrate (sampling / server-initiated
requests) is being deprecated as of 2026-07-28 [1][2].

---

## Open decisions to resolve in `/plan`

1. **Subscription-only policy vs. metered fallback.** splock forbids metered API
   keys today. Gemini CLI (the `gemini` binary) needs one; `agy` may not.
   Decide whether the policy admits a metered escape hatch or stays strict
   (which keeps Gemini on `agy` only).
2. **Verifier routing stance.** Always Claude-Haiku-pinned, or per-family fixed
   verifier? (Independence invariant either way.)
3. **How far to take Tier B.** Ship Tier-A routing only, or invest in the harness
   port? Recommend: Tier A first, revisit Tier B on evidence.
4. **`agy` provenance/billing.** Confirm the Antigravity CLI's official
   distribution and how its usage is billed before depending on it.

## Internal references

- `docs/PORTABILITY_REVIEW.md` — host feasibility (Codex/Antigravity).
- `docs/plans/portability_scaffolding/` — recon (host-adapter interface), qna
  (router seam, Tier A/B), research (LiteLLM north-star, routing taxonomy).
- `docs/plans/agent_teams/agent_teams_research.md` — teams / inter-agent comms.
- `docs/FLEET.md`, `bin/_fleet/*` — the spawn/state substrate multi-routing
  extends.

## Citations

Primary sources for the load-bearing claims above (retrieved 2026-07-20…07-22).
The 2026 agent-tooling blog ecosystem is heavily machine-generated; every item
below is a primary source (spec, official docs/blog, GitHub release/issue, arXiv,
or a locally-verified binary), and provider-internal numbers are marked as such.

**Standards / protocols**
1. A2A releases — github.com/a2aproject/A2A/releases (v1.0.0, 2026-03-12; LF-hosted, Apache-2.0).
2. MCP sampling deprecated + no server-initiated messages — modelcontextprotocol.io/specification/draft/client/sampling and …/basic/transports (protocol v2026-07-28).

**Multi-agent evidence (teams track)**
3. MAST, "Why Do Multi-Agent LLM Systems Fail?" — arXiv:2503.13657 (2025-03-17; ~37% inter-agent-comms failures; measured).
4. Cognition, "Don't Build Multi-Agents" — cognition.com/blog/dont-build-multi-agents (2025-06-12; argument, not benchmark).
5. Cognition interview, Latent Space — latent.space/p/cognition (2026-05-28; softened, not recanted).
6. Anthropic, "How we built our multi-agent research system" — anthropic.com/engineering/multi-agent-research-system (2025-06-13; +90.2% / ~15x tokens, provider-internal; coding carve-out).
7. Anthropic, "Building a C compiler with a team of parallel Claudes" — anthropic.com/engineering/building-c-compiler (2026-02-05; provider-internal).
8. AgentPrune, "Cut the Crap" — arXiv:2410.02506 (ICLR 2025; pruning chatter $43.7→$5.6, +3.5–10.8%; measured).
9. Tran & Kiela, single- vs multi-agent under equal compute — arXiv:2604.02460 (2026-04-02; unvetted preprint).
10. Reflexion — arXiv:2303.11366 (2023-03; +11 pts HumanEval; measured, the critic/verifier basis).
11. Blackboard data-science system — arXiv:2510.01285 (2025-09; 13–57% end-to-end gain; preprint).

**OpenAI Codex CLI**
12. Codex non-interactive mode / flag surface — learn.chatgpt.com/docs/non-interactive-mode (anchor: rust-v0.145.0, 2026-07-21).
13. Codex CI/CD auth (headless `~/.codex/auth.json`) — learn.chatgpt.com/docs/auth/ci-cd-auth.
14. Codex `--output-schema` limits — github.com/openai/codex issues #15451 (ignored w/ active MCP/tools), #14343 / #22998 (incompatible with `exec resume`).

**Google Gemini CLI / Antigravity**
15. Gemini CLI headless mode — github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md.
16. Gemini CLI + AI Pro/Ultra higher limits (historical) — blog.google, "developers-tools/gemini-cli-code-assist-higher-limits" (2025-09-24).
17. Gemini CLI → Antigravity CLI transition — developers.googleblog.com, "transitioning-gemini-cli-to-antigravity-cli" (2026-05-19).
18. Consumer login removed for Gemini CLI (free / AI Pro / AI Ultra), effective 2026-06-18 — developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals (page updated 2026-06-23).
19. Gemini CLI has no custom output schema — github.com/google-gemini/gemini-cli issues #13388, #5021, #12692.
20. `agy` (Antigravity CLI) v1.1.2 headless surface (`-p`/`--print`, `--conversation`, `--continue`, `--model`, `--mode`, `--sandbox`, `--dangerously-skip-permissions`) — **verified locally from `agy --help` on this machine, 2026-07-22** (supersedes web-research "no scriptable surface").

**Local coordination / security (teams track)**
21. Claude Code Agent Teams (JSON-file mailboxes + shared task list; experimental) — code.claude.com/docs/en/agent-teams.
22. No mid-turn message delivery to a running agent — github.com/anthropics/claude-code issue #21419 (closed as duplicate).
23. "Multi-Agent Systems Execute Arbitrary Malicious Code" (confused-deputy via subagent-read content) — arXiv:2503.12188 (2025-03; ~97% success).
24. Claude Code subagent-read injection hardening + invariant ("content read by a subagent cannot grant permission…") — anthropics/claude-code issue #77644 (2026-07-15, fixed v2.1.210).
25. Terrarium (blackboard-poisoning propagation) — arXiv:2510.14312 (2025-10).

**Routing design (internal research doc, itself primary-cited)**
26. git-lock contention destroys worktree-agent work at scale — anthropics/claude-code issue #55724 (2026-05-03).
27. LiteLLM in-process `Router` as design reference ("model after, never import") + route-once-per-role sticky / per-message anti-pattern under prompt-cache affinity — `docs/plans/portability_scaffolding/portability_scaffolding_research.md` §3, §5 (which cite LiteLLM Router and OpenRouter session stickiness as primaries).
