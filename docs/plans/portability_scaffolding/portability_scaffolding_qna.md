# portability_scaffolding — qna

**Question:** Review docs/PORTABILITY_REVIEW.md. We eventually want to build a dynamic routing layer that selects model families based on intents, rules, or subjects (e.g., mapping /research to Antigravity, /code to Claude, or the subject of cybersecurity to Codex). I want to defer the actual implementation until after Phase 2 (once Codex and Antigravity are fully supported), but we need to do the architectural prep work now during Phase 0. How should we extend our existing "intent registry" and Phase 0 "Model Transport" interfaces to eventually support this kind of dynamic routing? Additionally, what configuration or metadata fields should we start adding to the frontmatter of our commands/skills markdown files to prepare for these future routing rules?

**Answer:**

**A load-bearing observation first: there are two routing tiers, and only one is Python-routable.** Splock makes model calls in two ways. Tier A is programmatic — the two-call planner (`bin/_planner/two_call.py`), QA invoke (`bin/_qa/invoke.py`), and the retry-loop coder/reviewer/verifier spawners (`bin/_retry_loop/sdk_spawners.py`, injected via `bin/_chain_overnight/phase_spawn.py`). These all live in `bin/*` and are exactly where a `TransportRouter` can dynamically pick a family per call. Tier B is the interactive session itself: when the operator types `/research` inside Claude Code, the hosting REPL *is* the model family — splock cannot re-route that session to Antigravity from inside it. Post-Phase-2, "map /research to Antigravity" realistically means the skill's work is delegated through a routed `ModelTransport` (e.g., the research pass runs via the Antigravity transport's `spawn_agent`/`complete`, wrapping its headless CLI) rather than the hosting session switching platforms. This is why the correct Phase 0 prep is (1) threading a router seam through the three Tier-A DI points and (2) making the routing *inputs* (intent, subjects, capabilities) first-class data — which is precisely what the frontmatter/registry work below does.

**Part 1 — Extend the Phase 0 Model Transport surface (recon §2.D) with a router seam, vocabulary-now / rules-later.**

The recon's `ModelTransport`/`ModelCatalog`/`ModelRole`/`ModelPin` design already isolates *how* to call one family; dynamic routing needs a layer above that decides *which* family. Five additive extensions to the `bin/_host/` package, all cheap now and expensive to retrofit after two more adapters ship:

1. **Keyed transport registry, not singleton detection.** `bin/_host/registry.py` (recon work item 1) should expose `register_transport(family: str, transport: ModelTransport)` / `get_transport(family: str) -> ModelTransport` with `"claude"` as Phase 0's only registrant — a `dict`, not an env-sniffing singleton. The recon's `HostHookShim.detect()` pattern is right for hooks (the host platform is a runtime fact) but wrong for transports (the family is a routing *choice*).
2. **Separate `host` from `family` on `ModelTransport`.** The recon's `host: ClassVar[str]` conflates the hosting platform with the model family. They're 1:1 today, but keep two ClassVars (`host`, `family`) so a future API-key transport (e.g., Gemini-via-API inside a Claude Code session — the actual mechanism for Tier-B delegation) doesn't break the vocabulary. This also avoids a second collision: `agent_sessions.host` already means *machine hostname* (`socket.gethostname()`, `bin/_intent/register.py:402`) — routing fields must never reuse the bare name `host`.
3. **Add `capabilities() -> frozenset[str]` to the `ModelTransport` ABC now.** Returning e.g. `{"structured-output", "streaming", "agent-spawn", "web-search", "subagent-transcripts"}` for `ClaudeTransport`. Router eligibility becomes `required_capabilities <= transport.capabilities()`, and the capability tags double as the vocabulary for the frontmatter `requires` field (Part 3). PORTABILITY_REVIEW.md §3's platform differences (no SessionStart on Antigravity, hook-bypass on Codex, schema dialects) are exactly capability-matrix facts.
4. **Define the router seam with frozen dataclasses + a static Phase 0 implementation:**

   ```python
   @dataclass(frozen=True)
   class RouteQuery:
       role: ModelRole
       intent: str | None = None            # canonical command/skill intent key, e.g. "research"
       intent_kind: str | None = None       # bin/_intent KIND: interactive|chain_overnight|read_only_recon|read_only_review
       subjects: tuple[str, ...] = ()       # e.g. ("cybersecurity",)
       slug: str | None = None
       required_capabilities: frozenset[str] = frozenset()

   @dataclass(frozen=True)
   class RouteDecision:
       family: str                          # registry key
       rule_id: str                         # "static-default" in Phase 0; forensics
       reason: str = ""

   class TransportRouter(abc.ABC):
       @abc.abstractmethod
       def route(self, query: RouteQuery) -> RouteDecision: ...

   class StaticRouter(TransportRouter):     # Phase 0's ONLY implementation
       def route(self, query): return RouteDecision(family="claude", rule_id="static-default")
   ```

   Critically, the router selects the **family only**; the family's `ModelCatalog` + existing `OVERNIGHT_*` env pins select the model *within* the family. Existing precedence (`OVERNIGHT_CHAIN_PLANNER_MODEL` > auto-latest-Opus discovery > `DEFAULT_PLANNER_MODEL`, `two_call.py:472-481`; same pattern for QA/reviewer/coder) is untouched, and the verifier-independence invariant stays a catalog contract (`fixed=True` for `ModelRole.VERIFIER` in every registered family, recon Recommendation 3) that the router *cannot* override — a router picks which family's fixed verifier runs, never a different verifier model.
5. **Thread `RouteQuery` through the three Tier-A DI seams now, defaulting to `StaticRouter`.** `two_call._default_client()` and `_qa.invoke._default_client()` gain an optional `router` parameter and build `RouteQuery(role=PLANNER/QA, slug=…)`; `opus_adapter.build_adapters` / `phase_spawn.py:428-441` build per-role queries (CODER/REVIEWER/VERIFIER) and derive their injected `spawn_*_fn` callables from the routed transport's `spawn_agent`. After Phase 2, enabling dynamic routing is then exactly two changes — implement `RuleRouter` and ship its config — with **zero call-site changes**. The rule config should follow the existing three-layer settings precedent verbatim (`bin/_intent/settings.py`: `SPLOCK_SETTING__routing__*` env > JSON overlay at `${CLAUDE_PLUGIN_DATA}/` > default literal), as an ordered first-match-wins list `{match: {intent?, intent_kind?, role?, subjects?}, family, rule_id}` validated by a reserved `schemas/routing_rules_v1.schema.json`. Phase 0 reserves the settings namespace and schema filename; it does not implement matching.

**Part 2 — Extend the intent registry (`bin/_intent`) as both a routing *input* and a routing *ledger*.**

The §P intent registry is where per-session intent already lives (`kind`, `target_system_area`, `claimed_paths`, `originating_plan_slug`), so it is the natural source for `RouteQuery.intent_kind`/`slug` and the natural audit trail for decisions. Phase 0 prep, all additive:

1. **Leave `KIND` alone.** The closed set (`interactive`, `chain_overnight`, `read_only_recon`, `read_only_review`, `refusal.py:22-28`) is coarser than the operator's routing examples; the per-command intent signal is the skill/command intent key (which the recon's `SessionFacts.workflow_stage` already captures as "last splock slash-command seen"). Don't overload KIND with routing granularity.
2. **Add a `subjects` field to registration.** `bin/intent register --subjects cybersecurity,frontend` → a `subjects` JSON array on the local JSONL row and marker payload (nullable, default empty). This is where "the subject of cybersecurity" enters the system at session scope. Per the repo's closed-enum idiom, subjects should validate against a governed vocabulary (a `routing.subjects` settings knob or `docs/intent/subjects.txt`) rather than free text — the vocabulary itself can start empty and be a post-Phase-2 concern, but the field lands now.
3. **Add routing-forensics fields:** nullable `model_family` and `routing_rule_id` on the local JSONL row + marker payloads, written when a routed call serves the session. Defer the MySQL columns to the same additive-migration slug as the recon's G5 (`claude_session_id` rename) — and per the naming hazard above, never name these `host`.
4. **Reserve (don't implement) an `intent.routed` event name** in the `EVENT` closed enum — extending it is a code-only change by design (`refusal.py` docstring), but naming it now in the plan prevents collisions.
5. **Flag the orchestrator substrate for a post-Phase-2 schema bump.** `orchestrator_v1.schema.json::tasks[].agent_assignment` is `{subagent, model}` with `additionalProperties: false` — per-task family routing eventually needs an optional `model_family` key (default `"claude"`) plus family-aware `"inherit"` semantics, which is a schema-version bump per the roster's own governance note. Do not do this in Phase 0; record it.

**Part 3 — Frontmatter: use the Agent Skills `metadata` extension point for skills; use `agents/_roster.json` for agents; leave `commands/*.md` alone.**

Two hard constraints discovered here. First, CI runs `claude plugin validate . --strict` as a smoke-battery gate (`tests/test_smoke_battery.py:149-176`), and per current Claude Code behavior unrecognized frontmatter fields are warnings that `--strict` promotes to errors — so **inventing top-level custom keys (e.g. `splock-routing:`) in commands/agents frontmatter would break CI**. Second, the Agent Skills open standard (which Claude Code skills follow, and which Codex and Antigravity both consume per PORTABILITY_REVIEW.md §3/§5) defines an official optional `metadata` frontmatter field: "a map from string keys to string values… clients can use this to store additional properties not defined by the spec," with a recommendation to use uniquely-prefixed key names. That makes `metadata` in `skills/*/SKILL.md` the *portable, validator-sanctioned* home for routing hints — it travels with the skill to all three platforms and is exactly what the Phase-1 packaging generators will read. Concretely, per skill (values are strings per spec; lists are space-separated, matching the `allowed-tools` idiom):

```yaml
---
name: research
description: …existing…
metadata:
  splock-route-intent: "research"                      # canonical intent key; defaults to skill name — decouples routing rules from renames
  splock-route-default-family: "claude"                # today's only family
  splock-route-allowed-families: "claude"              # post-Phase-2: "claude codex antigravity"
  splock-route-requires: "web-search structured-output" # capability tags matched against ModelTransport.capabilities()
  splock-route-subject-affinity: ""                     # optional subject tags biasing family choice, e.g. "cybersecurity"
---
```

Design principle: frontmatter declares **static per-unit facts** (what this unit is, what capabilities it needs, which families are eligible); the central `routing_rules.json` holds the **dynamic rules** (subject→family mappings, operator overrides). "Cybersecurity → Codex" is a rule about a *request*, not about the `/research` command file, so it belongs in config — the frontmatter only supplies the intent key and eligibility envelope the rule engine consults.

For **agents**, the documented frontmatter vocabulary (`name`, `description`, `model`, `effort`, `maxTurns`, `tools`, `disallowedTools`, `skills`, `memory`, `background`, `isolation`) has no `metadata` field, so agent routing metadata should NOT be frontmatter. The right home already exists: `agents/_roster.json`, the hand-authored, schema-versioned source-of-truth for subagent roles. Bump it to `schema_version: 3` with an additive `routing` mapping (consumers that read only `subagents` stay compatible):

```json
{
  "schema_version": 3,
  "subagents": ["planner", "recon", "qa", "qna", "research", "coder", "reviewer", "verifier"],
  "routing": {
    "verifier": {"model_role": "verifier", "pin_policy": "fixed",
                 "model_pins": {"claude": "claude-haiku-4-5-20251001"}, "override_env": null},
    "coder":    {"model_role": "coder", "pin_policy": "env-overridable",
                 "model_pins": {"claude": "opus"}, "override_env": "OVERNIGHT_OPUS_CODER_MODEL"},
    "reviewer": {"model_role": "reviewer", "pin_policy": "env-overridable",
                 "model_pins": {"claude": "sonnet"}, "override_env": "OVERNIGHT_SONNET_REVIEW_MODEL"}
  }
}
```

This dovetails with recon work item 5 (`ClaudeModelCatalog` with the fixed verifier pin asserted in code): the catalog reads `model_pins.claude` from this file, `agents/verifier.md:5`'s `model:` line becomes a mirrored value the Phase-1 packaging generators emit per host, and the per-family verifier pins PORTABILITY_REVIEW.md §4 calls for ("GPT-5-mini-class / Gemini Flash-class") get their slots (`model_pins.codex`, `model_pins.antigravity`) filled at each adapter phase. A conformance test asserts every family key present in any `model_pins` is a registered transport family and every `pin_policy: fixed` role has a pin for every shipped family.

For **`commands/*.md`**: add nothing in Phase 0. Commands are merged into skills upstream (both create the same slash command), the eleven `skills/*/SKILL.md` files are the canonical, standard-bearing copies, and the command-frontmatter validator's tolerance for `metadata` is unverified — the strict gate makes experimentation there all risk, no reward. One caveat applies to skills too: I could not empirically test that `metadata:` passes the repo's `--strict` gate (the sealed-path hook correctly refused my scratchpad plugin-copy attempt — `agents/**`, `commands/**`, `skills/**` are sealed patterns that over-seal by design, `hooks/sealed_paths.txt:70-84`), so the rollout plan should add the block to ONE skill first, run the smoke battery, then roll out to the rest. All these files are sealed, so every frontmatter/roster task must go through the sanctioned CLI-managed process.

**Evidence:**

1. `/home/aiadmin/splock/docs/PORTABILITY_REVIEW.md:48-66` — hexagonal split, Model transport seam spec, packaging generators ("Frontmatter is the main translation (model pins, tool lists)"), Phase 0→2 sequencing; `:59` per-family verifier-pin policy.
2. `/home/aiadmin/splock/docs/plans/portability_scaffolding/portability_scaffolding_recon.md:289-381` — Phase 0 `ModelRole`/`ModelPin`/`ModelCatalog`/`CompletionRequest`/`AgentSpawnSpec`/`ModelTransport` design; `:384-404` work items incl. `registry.py`; `:428-445` recommendations incl. verifier `ModelCatalog` contract test.
3. `/home/aiadmin/splock/bin/_intent/__init__.py:1-7` — the intent registry: §P agent-session registry over `extraction.agent_sessions` + `docs/intent/intent_local.jsonl`.
4. `/home/aiadmin/splock/bin/_intent/refusal.py:1-28` — closed-enum discipline; `KIND = {interactive, chain_overnight, read_only_recon, read_only_review}`; docstring: extending an enum is a code-only change.
5. `/home/aiadmin/splock/bin/_intent/register.py:130-165` — registry row shape (kind, target_system_area, claimed_paths, originating_plan_slug, claude_session_id side column); `:402` `host = socket.gethostname()` (machine hostname — naming collision hazard with platform "host").
6. `/home/aiadmin/splock/bin/_intent/settings.py:1-30` — three-layer settings precedence (env `SPLOCK_SETTING__*` > JSON overlay at `${CLAUDE_PLUGIN_DATA}/intent_settings.json` > call-site default) — the precedent for `routing_rules` config resolution.
7. `/home/aiadmin/splock/bin/_planner/two_call.py:472-481` — planner model precedence: `OVERNIGHT_CHAIN_PLANNER_MODEL` > auto-latest-Opus discovery > `DEFAULT_PLANNER_MODEL`; family-internal resolution the router must not disturb.
8. `/home/aiadmin/splock/bin/_retry_loop/sdk_spawners.py:1636,1939,2204,2584` and `/home/aiadmin/splock/bin/_qa/invoke.py:103,370-375` — reviewer/coder/QA env-overridable pins (`OVERNIGHT_SONNET_REVIEW_MODEL`, `OVERNIGHT_OPUS_CODER_MODEL`, `OVERNIGHT_CHAIN_QA_MODEL`).
9. `/home/aiadmin/splock/bin/_chain_overnight/phase_spawn.py:428-441` — DI seam: `spawn_opus_fn`/`run_verify_fn`/`spawn_reviewer_fn` injected into `run_test_step_loop` via `build_adapters` — the Tier-A router insertion point.
10. `/home/aiadmin/splock/agents/verifier.md:1-6` — fixed verifier frontmatter pin `model: claude-haiku-4-5-20251001`; frontmatter survey command output — agents use `name`/`description`/`tools`(/`model`), commands use `description`/`argument-hint`, skills use `name`/`description` only.
11. `/home/aiadmin/splock/agents/_roster.json:1-14` — `schema_version: 2`, hand-authored source-of-truth enum; role changes require schema-version bump per implplan §B.impl.3 + §D.impl.4.
12. `python3` extraction of `/home/aiadmin/splock/schemas/orchestrator_v1.schema.json` — `tasks[].agent_assignment` = `{subagent, model}`, `additionalProperties: false`, `model` = `'inherit'` or literal; family field requires a schema bump.
13. `/home/aiadmin/splock/tests/test_smoke_battery.py:149-176` — CI gate runs `claude plugin validate . --strict`; unrecognized-frontmatter warnings become errors under `--strict` (web research, 2026-07-15: Claude Code docs/plugin-validator sources — unrecognized fields are warnings, `--strict` promotes warnings to errors).
14. `/home/aiadmin/splock/hooks/sealed_paths.txt:61-84` — `agents/**`, `commands/**`, `skills/**` sealed (documented over-sealing); empirically confirmed: my scratchpad plugin-copy write was refused by the sealed-state hook, so `metadata`-under-`--strict` remains unverified.
15. https://agentskills.io/specification (retrieved 2026-07-15) — official `metadata` frontmatter field: "map from string keys to string values… clients can use this to store additional properties not defined by the Agent Skills spec"; recommends uniquely-prefixed keys; `allowed-tools` space-separated-string idiom.
16. https://code.claude.com/docs/en/skills (retrieved 2026-07-15) — custom commands merged into skills; both `commands/deploy.md` and `skills/deploy/SKILL.md` create the same `/deploy`; CC follows the Agent Skills open standard.
17. Web search (2026-07-15, code.claude.com + github.com/anthropics sources) — documented agent frontmatter vocabulary (`name, description, model, effort, maxTurns, tools, disallowedTools, skills, memory, background, isolation` — no `metadata`); GH issue #25380 documents the SKILL.md validator rejecting non-standard extended frontmatter.

**Confidence:** High for the interface/registry design and the two-tier routing analysis (grounded in directly-read repo code and the recon's committed Phase 0 design); the single medium-confidence claim — that `metadata:` in SKILL.md passes the repo's `--strict` validate gate — is flagged and converted into an explicit one-skill-first verification task rather than assumed.

**Suggested follow-ups:**

- Fold the recommendations below into `/plan portability_scaffolding` (they are Phase 0 scope additions, not a new slug).
- Add to the already-recommended `/research portability_scaffolding` pass (recon G1-G3): (G8) empirically verify `claude plugin validate --strict` accepts the Agent Skills `metadata` field in SKILL.md and enumerate the recognized command-frontmatter vocabulary; (G9) confirm Codex and Antigravity preserve/expose skill `metadata` at import time (Codex's CC-importer and Antigravity's `.agents/skills/` loader).
- Reserve a separate post-Phase-2 slug (e.g. `dynamic_model_routing`) for the actual `RuleRouter`, `routing_rules_v1.schema.json` + matching engine, subjects vocabulary governance, the `intent.routed` event, the MySQL `model_family`/`routing_rule_id` columns (bundled with the G5 migration), and the `agent_assignment.model_family` orchestrator schema bump.

## Recommendations for /plan

1. **Extend Phase 0 work item 1** (recon §3.1, `bin/_host/` package): make `registry.py` a keyed `register_transport(family)`/`get_transport(family)` registry; add `family: ClassVar[str]` alongside `host` on `ModelTransport`; add abstract `capabilities() -> frozenset[str]`; add `RouteQuery`/`RouteDecision` frozen dataclasses and the `TransportRouter` ABC with `StaticRouter` as the sole Phase 0 implementation (new module `bin/_host/routing.py`).
2. **Extend Phase 0 work item 4** (recon §3.4): when routing `_default_client()` (planner + qa) and `phase_spawn`/`opus_adapter.build_adapters` through `ClaudeTransport`, thread an optional `router: TransportRouter = StaticRouter()` parameter and construct per-role `RouteQuery` values at each seam — acceptance: dynamic routing later requires zero call-site changes.
3. **Extend Phase 0 work item 5** (recon §3.5): move the role→pin data into `agents/_roster.json` `schema_version: 3` with the additive `routing` mapping (`model_role`, `pin_policy`, `model_pins.{family}`, `override_env`); `ClaudeModelCatalog` reads from it; keep `agents/verifier.md` `model:` as the CC-native mirror; add a conformance test that every `pin_policy: fixed` role has a pin for every registered family and that env overrides are ignored for fixed pins.
4. **New Phase 0 task — skill routing metadata:** add the `metadata:` block with `splock-route-intent`, `splock-route-default-family`, `splock-route-allowed-families`, `splock-route-requires`, `splock-route-subject-affinity` (string values, space-separated lists) to `skills/*/SKILL.md`; rollout gated by adding it to ONE skill first and passing `tests/test_smoke_battery.py`'s `--strict` gate; do NOT add custom keys to `commands/*.md` or `agents/*.md` frontmatter. All touched files are sealed — tasks must use the sanctioned edit path.
5. **New Phase 0 task — intent-registry routing fields (additive, JSONL + markers only):** optional `subjects` array (+ `--subjects` CLI arg on `bin/intent register`), optional `model_family` and `routing_rule_id` keys; defer MySQL columns to the G5 migration slug; forbid naming any new field `host` (collision with machine-hostname column, `register.py:402`).
6. **Reservations (documented in plan, not implemented):** settings namespace `routing.*` + overlay filename `routing_rules.json`; schema filename `schemas/routing_rules_v1.schema.json`; `intent.routed` member of the `EVENT` enum; post-Phase-2 `agent_assignment.model_family` orchestrator schema-version bump.
