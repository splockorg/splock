I now have full grounding. Key confirmations: `bin/_host/` and `tests/host_conformance/` don't exist yet (all-new); roster is at `schema_version: 3` (bump to 4 is correct); the Tier-A coder/reviewer/verifier DI seam is the single factory `bin/_retry_loop/opus_adapter.py::build_adapters` (line 114), called from three sites (`phase_spawn.py:428`, `main.py:529`, `main.py:692`); `iteration_loop.py` stays untouched; `sanitize_schema`/`strip_schema_meta_keys` and `NESTED_SESSION_ENV_VARS` land in `bin/_sdk_bridge.py`/`bin/_host/transport.py`; sealed roots are `agents/** commands/** hooks/** skills/**` while all of `bin/**` (including `bin/security-dispatch.sh` and the new `bin/hook-entry`) is freely editable.

Here is my implplan Call 1 decomposition.

---

# Preamble — grounding, invariants, and cross-cutting decisions for Call 2

This is the implplan promotion of an already-schema-valid plan substrate (SC1–SC10) into orchestrator tasks. The plan substrate is unusually complete; my job is to attach, per task, the concrete **file_paths_touched** (with call-site anchors verified against the live repo), **runnable pytest selectors** for `tests_enabled`, **test_plan** narrative, **dependencies**, and **sealed-path handling**. I keep the SC↔task mapping and `depends_on` graph from `tasks_skeleton` intact; they are correct.

**Repo facts confirmed by recon (so Call 2 can trust the anchors):**
- `bin/_host/` — does not exist; entirely new package.
- `tests/host_conformance/` — does not exist; entirely new test package (needs `__init__.py`).
- `agents/_roster.json` — currently `schema_version: 3`, nine subagents (`planner, recon, qa, qna, research, coder, reviewer, verifier, eli5`). SC5's "bump to 4" is correct.
- Model-pin constants live at: `two_call.py:74` (`DEFAULT_PLANNER_MODEL="claude-opus-4-8"`), `_qa/invoke.py:103` (`DEFAULT_QA_MODEL="claude-opus-4-8"`), `sdk_spawners.py:1636` (`_DEFAULT_REVIEWER_MODEL="sonnet"`), `sdk_spawners.py:2204` (`_DEFAULT_CODER_MODEL="opus"`), `_eli5/invoke.py:36` (`DEFAULT_ELI5_MODEL="claude-opus-4-8"`), `agents/verifier.md:5` (`claude-haiku-4-5-20251001`, fixed). That is **6** roles with explicit pins; `recon/qna/research` have none — the ModelRole "9-vs-10th count" is a genuine resolve-at-implementation item (below).
- Tier-A DI factory: `bin/_retry_loop/opus_adapter.py::build_adapters` (line 114) returns `(_opus_adapter, _verify_adapter, _reviewer_adapter)`; called at `phase_spawn.py:428`, `main.py:529`, `main.py:692`. `iteration_loop.py` is the injection *consumer* and must NOT be touched.
- Transport internals: `_sdk_bridge.py` `SubscriptionClient` (496), injectable `query_fn`/`options_cls` (517–522), `_force_subscription_auth` (287–306) applied in `_drive_query` (338), `strip_schema_meta_keys` (199), `_AdaptedMessage` (117) + `_STRUCTURED_RETRY_SUBTYPE="error_max_structured_output_retries"` (86), `_Messages._build_options` calls `strip_schema_meta_keys(output_format)` at 365.
- Hook trampolines: `bin/security-dispatch.sh` (**in `bin/`, NOT sealed**) pipes stdin into `python -m bin._hooks.security_dispatch`; `security_dispatch.py` has `_is_deny_envelope` asserting `permissionDecision=="deny"` (85). The 14 other trampolines live in `hooks/` (**sealed**).
- Intent: `register.py:402` `host = socket.gethostname()` (the naming-collision hazard), `claude_session_id` column handling, marker payload construction (~521). `refusal.py` holds the closed enums (`KIND`, `EVENT`, etc.). `settings.py` is the three-layer resolver (`SPLOCK_SETTING__…` > overlay JSON > default).
- Sealed roots: `agents/** commands/** hooks/** skills/** .plugin-data/**`. **All of `bin/**` and `docs/**` (except specific `docs/plans/*/_*` state globs) are editable.**

**Cross-cutting decisions I'm baking into Call 2:**

1. **Sealed-path routing.** SC2's *first* cutover (security-dispatch) requires **no sealed edits** — `bin/security-dispatch.sh` (editable) keeps its path and filename; only its body changes to `exec bin/hook-entry security-dispatch`, so `hooks/hooks.json` is untouched. The remaining 14 `hooks/*.sh` trampolines are sealed and are explicitly **out of SC2's Phase-0 acceptance** (they keep firing the old `python -m …` path — zero behavior change — and get cut over incrementally later via the sanctioned edit path). SC5 (`agents/**`) and SC7 (`skills/**`) *do* require the sanctioned CLI-managed edit path; both carry a sealed-path note.

2. **Zero-behavior-change preservation of the model constants.** SC5 introduces `ClaudeModelCatalog` reading `agents/_roster.json` as the new source-of-truth *data location* and asserts equivalence with today's scattered constants — but does **not delete** those constants, because the call sites (`two_call`, `_qa.invoke`, `sdk_spawners`) still read them directly until routing is live. Deleting them would be a behavior-change risk with no Phase-0 payoff. The catalog is additive; migration of call sites onto it is a later-phase concern.

3. **SC10 file-overlap is intentional and safe.** The conformance-gate task must, per the `tests_enabled` contract, list every selector's path in its own `file_paths_touched`. So SC10's `file_paths_touched` re-lists the 13 conformance test files plus `tests/test_smoke_battery.py`. Because SC10 `depends_on` SC1–SC8, it runs strictly after those files exist — no concurrent-write collision. SC10's only *new writes* are `tests/host_conformance/conftest.py`/`__init__.py` (collection wiring); it must not weaken any assertion authored upstream. I flag this as a guardrail note on the task.

4. **`tests_enabled` are file-level selectors** (e.g. `tests/host_conformance/test_events.py`) — guaranteed runnable since the test functions don't exist yet; the specific gating functions (deny-parity, nested-scrub, catalog-contract) are named in each task's `test_plan`, not in `tests_enabled`.

5. **Suggested phase grouping** (from the substrate overview, under the standing CI-green invariant): **0a** = SC1+SC4+SC5 (transport substrate) → **0b** = SC2+SC3 (hook/transcript extraction behind deny-parity) → **0c** = SC6+SC7+SC8 (routing seam + metadata + intent fields) → **0d** = SC9+SC10 (docs + acceptance gate). The `depends_on` graph already encodes this; SC8 and SC9 are independent and can float earlier.

---

## SC1 — Host-neutral hook vocabulary (`bin/_host/events.py`)

**Failure modes addressed.** (a) A downstream port accidentally imports host code into the shared vocabulary, re-coupling the "neutral" layer to Claude Code. (b) A boundary value is mutated in-flight by one hook and bleeds state into another (the reason `frozen=True` is load-bearing). (c) Enum drift — a host maps a native tool onto an unrecognized `ToolClass`, or the verdict set silently grows beyond `{allow, deny, warn}`. (d) `USER_PROMPT_SUBMIT`/`SUBAGENT_STOP` getting dropped as second-class because some hosts emulate them.

**File paths & call sites touched.**
- `bin/_host/__init__.py` (new; empty or lazy-export shim per repo idiom).
- `bin/_host/events.py` (new; `HookEventKind`, `ToolClass`, frozen `HookEvent`/`HookDecision`/`HookOutcome`). Zero imports beyond stdlib (`enum`, `pathlib`, `dataclasses`, `typing`). No I/O.
- `tests/host_conformance/__init__.py` (new).
- `tests/host_conformance/test_events.py` (new).

**tests_enabled.** `tests/host_conformance/test_events.py`

**test_plan.**
- `test_events_no_host_imports` — import `bin._host.events` and assert its module `__dict__`/source contains no reference to `bin._hooks`, `bin._intent`, `claude_agent_sdk`, or any `bin._host.shim/transcript/transport` sibling (import-isolation guard; the substrate's core SC1 assertion).
- `test_events_frozen` — constructing then mutating any of the three dataclasses raises `dataclasses.FrozenInstanceError`.
- `test_events_enum_closed_sets` — `HookEventKind`, `ToolClass`, and the `HookDecision.verdict` `Literal` expose exactly the specified members (`warn` present but asserted unused elsewhere).

**Dependencies.** None (root of the port DAG).

**Notes.** Editable (`bin/`, `tests/`). This is the SC1 substrate every other port imports; keep it dependency-free so the import-isolation guard stays trivially true.

---

## SC2 — Hook Shim port + `hook-entry` dispatcher + trampoline cutover (deny-parity gated)

**Failure modes addressed.** (a) The cutover changes the deny envelope even one byte → Claude Code silently stops enforcing sealed-path/security refusals (the highest-severity regression in the whole plan). (b) A parse error on malformed stdin throws instead of failing open → a hook crash blocks a legitimate tool call (violates the exit-0-always contract). (c) `detect()` misroutes an unknown host to something other than the Claude shim. (d) Scope creep: touching the 14 sealed `hooks/*.sh` trampolines in Phase 0 and destabilizing live enforcement.

**File paths & call sites touched.**
- `bin/_host/shim.py` (new; `HostHookShim` ABC with `parse_event(stdin_payload, env)->HookEvent`, `render_decision(decision, event)->HookOutcome`, classmethod `detect(env)->HostHookShim` registry dispatch; `ClaudeHookShim` the only registrant — parses the CC PreToolUse/lifecycle envelope, renders exit-0 + `permissionDecision:deny` JSON on stdout).
- `bin/hook-entry` (new; executable dispatcher: `detect(os.environ)` → `parse_event` → named core hook (`HookEvent->HookDecision`) → `render_decision` → emit `HookOutcome`).
- `bin/_hooks/security_dispatch.py` (refactor: expose a pure `security_decision(event: HookEvent) -> HookDecision` callable that `hook-entry` invokes; keep the existing `main()` stdin path intact for back-compat so nothing else regresses).
- `bin/security-dispatch.sh` (**editable**; body → `exec "$CLAUDE_PLUGIN_ROOT/bin/hook-entry" security-dispatch`; filename/registration unchanged so `hooks/hooks.json` is untouched).
- `tests/host_conformance/test_shim_claude.py` (new).
- `tests/host_conformance/test_deny_parity.py` (new).

**tests_enabled.** `tests/host_conformance/test_shim_claude.py`, `tests/host_conformance/test_deny_parity.py`

**test_plan.**
- `test_deny_parity` (THE acceptance gate for the cutover) — for a battery of golden CC PreToolUse stdin payloads that today produce a deny (sealed-path write, unsafe DDL, package-safety, claude-md-discipline), assert `bin/hook-entry security-dispatch` emits **byte-for-byte identical stdout** and identical exit code to the legacy `python -m bin._hooks.security_dispatch` path. No trampoline is considered cut over until this is green for it.
- `test_shim_parse_event` — golden CC payloads → expected `HookEvent` (tool-name→`ToolClass` mapping `Edit|Write→FILE_WRITE`, `Read→FILE_READ`, `Bash→SHELL`, `Task→AGENT_SPAWN`; `file_path`/`command`/`prompt`/`source` normalization).
- `test_shim_render_decision` — `HookDecision(deny)` → exit-0 `HookOutcome` with the CC `permissionDecision:deny` JSON; `allow` → silent exit-0.
- `test_shim_fail_open` — malformed/truncated stdin yields an ALLOW `HookOutcome` (exit 0, no stdout deny) plus a hook-log row; never raises.
- `test_detect_registry` — `CLAUDE_*` env → `ClaudeHookShim`; unknown host → `ClaudeHookShim` + a warning row.

**Dependencies.** SC1 (imports the vocabulary).

**Notes.** Verify sealed status before any `hooks/**` write — but Phase-0 scope is deliberately confined to the editable `bin/security-dispatch.sh` cutover; the 14 sealed `hooks/*.sh` trampolines stay on the legacy path (zero behavior change) and are named as incremental follow-through, not part of SC2's acceptance. `hooks/permissions.deny` stays CC-only, unchanged.

---

## SC3 — Transcript Provider port + `hook_writer` refactor to `SessionFacts`

**Failure modes addressed.** (a) The extraction changes what gets scraped → the intent registry's Phase-B columns (`workflow_stage`, `tools_used_count`, `files_touched`, `live_status`, subagent records) drift from today's values. (b) A missing/corrupt/empty CC jsonl makes the provider raise → a hook crash instead of degrading to empty facts (violates `hook_writer.py:12-13` fail-open). (c) The refactor entangles DB-upsert logic with scraping logic, so a future non-Claude provider can't substitute. (d) The provider is forced to do file I/O even for hosts that ship facts in-payload.

**File paths & call sites touched.**
- `bin/_host/transcript.py` (new; `TranscriptProvider` ABC (`transcript_path`, `session_facts`, `subagent_records`); frozen `FileTouch`/`SubagentRecord`/`SessionFacts` (all fields optional); `ClaudeTranscriptProvider` absorbing the scraping battery).
- `bin/_intent/hook_writer.py` (**editable**; move `_claude_project_dir` (58), `_jsonl_path` (64), `_read_tail` (68), the regex extractors `_extract_recent_prompts`/`_extract_tools_used_count`/`_extract_files_touched`/`_extract_todo_state`/`_extract_workflow_stage`/`_extract_signals` (93–257), `_live_status_for` (259), and `_find_recent_subagent_file`/`_upsert_subagent`'s scraping half (379–458) into `ClaudeTranscriptProvider`; reduce `hook_writer` to the DB-upsert code (`_ensure_row_exists` 284, `_apply_signals` 320, `_cmd_*` 460–535) consuming a `SessionFacts`/`SubagentRecord`).
- `tests/host_conformance/test_transcript_claude.py` (new).

**tests_enabled.** `tests/host_conformance/test_transcript_claude.py`

**test_plan.**
- `test_session_facts_golden` — a golden CC jsonl fixture → expected `SessionFacts` (custom_title, git_branch, workflow_stage, recent_prompts, tools_used_count, files_touched, live_status) and `subagent_records` equality.
- `test_transcript_fail_open` — corrupt and empty/missing fixtures both return an empty `SessionFacts` and never raise.
- `test_hook_writer_upsert_equivalence` — driving the refactored `hook_writer` from a `SessionFacts` produces the same DB-upsert calls (same columns/values) as the pre-refactor scraping path against the same fixture (regression net).

**Dependencies.** SC1.

**Notes.** Editable. Record (do NOT implement) the recon-G5 flag: `extraction.agent_sessions.claude_session_id` / `agent_subagents.parent_claude_session_id` are henceforth read as "host session id"; the rename/alias migration is deferred and recorded in SC9. Preserve the fail-open contract verbatim.

---

## SC4 — Model Transport port + `ClaudeTransport` (wrap) + `sanitize_schema` + nested-session env scrub

**Failure modes addressed.** (a) **The empirically-confirmed nested-session deadlock** (2026-07-22): an SDK-spawning splock command invoked inside a live Claude Code session hangs because the nested `claude` inherits eight session-identity env vars. (b) A `CLAUDE_*`-prefix wipe (instead of an explicit denylist) strips `CLAUDE_PLUGIN_ROOT`/`CLAUDE_PLUGIN_DATA`/`CLAUDE_PROJECT_DIR` and breaks the host-neutral env contract. (c) The scrub is not a no-op when the vars are unset → behavior drift on the normal (non-nested) path, violating the global zero-change gate. (d) A rewrite (rather than a wrap) of `_sdk_bridge`/`sdk_spawners` regresses the proven `_AdaptedMessage`/`_STRUCTURED_RETRY_SUBTYPE` mapping. (e) The CLI-dialect `sanitize_schema` transform mangles a schema (drops `enum`, forgets `additionalProperties:false`, fails to rewrite `const`) and a future Codex emission silently mis-validates.

**File paths & call sites touched.**
- `bin/_host/transport.py` (new; `ModelTransport` ABC (`complete`/`stream`/`spawn_agent`/`sanitize_schema`); `ModelRole` enum, frozen `ModelPin`, `ModelCatalog` ABC, `CompletionRequest`/`CompletionResult`/`AgentSpawnSpec`; `ClaudeTransport` wrapping `_sdk_bridge.SubscriptionClient` for `complete`/`stream` and `sdk_spawners` for `spawn_agent`; the build-now pure `sanitize_schema` transform; the `NESTED_SESSION_ENV_VARS` constant + a shared pure scrub helper; `ClaudeTransport.spawn_agent` applies the scrub to its effective-env before delegating to `sdk_spawners`).
- `bin/_sdk_bridge.py` (**editable**; add the `NESTED_SESSION_ENV_VARS` scrub at the `claude`-subprocess spawn boundary — inside/around `_drive_query` (309–343) alongside `_force_subscription_auth` (287–306) as a second spawn-boundary env guard; import the shared constant so `_sdk_bridge` and `transport` agree on the exact eight names). Keep `strip_schema_meta_keys` (199) as the live Claude sanitizer — `ClaudeTransport.sanitize_schema` delegates to it; the *new* CLI-dialect `sanitize_schema` ships as a tested pure function but is NOT wired into any live transport (CodexTransport deferred).
- `tests/host_conformance/test_transport_claude.py` (new).
- `tests/host_conformance/test_sanitize_schema.py` (new).
- `tests/host_conformance/test_nested_session_scrub.py` (new).

**tests_enabled.** `tests/host_conformance/test_transport_claude.py`, `tests/host_conformance/test_sanitize_schema.py`, `tests/host_conformance/test_nested_session_scrub.py`

**test_plan.**
- `test_nested_session_scrub` (SC4 affirmative proof, guards three failure modes) — using the injectable `query_fn`/`options_cls` seam (`_sdk_bridge.py:517–522`) and a captured-env stub for the `spawn_agent` path (no real `claude` launched): set all eight vars in a parent env, drive both spawn paths, assert the effective child env **excludes all eight** (against `NESTED_SESSION_ENV_VARS`) → guards *incomplete scrub*; assert `CLAUDE_PLUGIN_ROOT`/`CLAUDE_PLUGIN_DATA`/`CLAUDE_PROJECT_DIR` **survive** → guards *over-scrub*; assert an env without the eight is **byte-identical after scrub** → guards *behavior drift / no-op-when-unset*.
- `test_sanitize_schema` (gates SC4 per SC10) — (i) the Claude `strip_schema_meta_keys` round-trip stays byte-stable against golden schemas including `schemas/plan_v1.schema.json`; (ii) the new CLI-dialect transform strips `$schema`/`$id`/`$comment`, recursively sets `required=all-properties` + `additionalProperties:false`, rewrites `const`→single-value `enum`, drops `minLength`/`maxLength`/`pattern`/`minItems`/`maxItems`/`format`, keeps `enum`; (iii) a canned gpt-5.6-style emission validates against the **original strict** schema.
- `test_transport_claude` — `ClaudeTransport.complete`/`stream` map `_AdaptedMessage`→`CompletionResult` (including `subtype==error_max_structured_output_retries`→`schema_exhausted`); `spawn_agent` delegates to `sdk_spawners` and returns a `CompletionResult`; `sanitize_schema` delegates to `strip_schema_meta_keys` for the Claude dialect.
- Regression: existing `tests/test_sdk_bridge_schema_meta.py` and `tests/test_planner_subscription_transport.py` stay green (SC10 union).

**Dependencies.** SC1. (SC5 and SC6 depend on SC4.)

**Notes.** Editable. Wrap-not-rewrite is a hard rule; `iteration_loop.py` untouched. The scrub's only intended behavior change is nested invocations going deadlock→success (a strict capability gain).

---

## SC5 — `ClaudeModelCatalog` + roster `schema_version: 4` + verifier-independence contract

**Failure modes addressed.** (a) A future host family (or a careless env override) makes the verifier non-independent by overriding its fixed Haiku pin → breaks the Ralph-gate determinism/cost contract. (b) Role→pin data scattered across five modules drifts out of sync. (c) The roster bump breaks a consumer that reads `subagents` or pins `schema_version==3`. (d) `ModelRole` membership is under-counted (missing `recon/qna/research/eli5`) so a role resolves to nothing.

**File paths & call sites touched.**
- `bin/_host/transport.py` (**editable**, sequential after SC4; add concrete `ClaudeModelCatalog.resolve(role)->ModelPin` reading `agents/_roster.json` `routing`; verifier pin `fixed=True` with env override ignored; overridable pins honor `ModelPin.override_env` preserving the `OVERNIGHT_*` > auto-latest-Opus > default precedence at `two_call.py:497` and analogues).
- `agents/_roster.json` (**SEALED — sanctioned edit path**; `schema_version` 3→4; additive `routing` mapping `{role: {model_role, pin_policy ∈ {fixed, env-overridable}, model_pins.{family}, override_env}}`; `subagents` list unchanged so existing consumers stay compatible).
- `agents/verifier.md` (**SEALED**; `model:` line stays as the CC-native mirror of `model_pins.claude` — likely **no edit needed** since the value is unchanged; flag only).
- `tests/host_conformance/test_catalog_claude.py` (new).
- `tests/host_conformance/test_roster_schema.py` (new).

**tests_enabled.** `tests/host_conformance/test_catalog_claude.py`, `tests/host_conformance/test_roster_schema.py`

**test_plan.**
- `test_catalog_verifier_independence` (gates SC5 per SC10) — every registered family returns `fixed=True` for `ModelRole.VERIFIER`; setting a verifier-override / `OVERNIGHT_*` env var does **not** change the resolved verifier model.
- `test_catalog_overridable_precedence` — for PLANNER/QA/CODER/REVIEWER, `resolve()` honors `ModelPin.override_env` and reproduces today's precedence chain.
- `test_roster_schema_v4` — `schema_version==4`; every `model_pins` family key is a registered transport family; every `pin_policy:fixed` role has a pin for every shipped family; `routing.verifier.model_pins.claude` equals `agents/verifier.md`'s `model:`.
- `test_model_role_completeness` — resolve the ModelRole set against the codebase: enumerate from the union of `DEFAULT_*_MODEL` constants and roster subagents, and assert the empirically-resolved membership (this is where the "9-vs-10th count" is nailed down — decide whether `recon/qna/research` get own pins or map to a shared PLANNER-family default, and pin it).
- Regression: `tests/test_verifier_model_pin_required.py` and any roster consumer (`tests/test_agent_twins_match_the_engine.py`, `tests/test_shipped_surfaces_have_engines.py`) stay green (SC10 union) — **implementation-time check**: confirm none pins `schema_version==3`.

**Dependencies.** SC4.

**Notes.** Sealed `agents/**`. Do not delete the scattered `DEFAULT_*_MODEL` constants (zero-behavior-change; call sites still read them until routing is live).

---

## SC6 — Transport registry + routing seam (StaticRouter) + Tier-A DI threading

**Failure modes addressed.** (a) An env-sniffing singleton (correct for hooks, wrong for transports) forecloses per-call family choice. (b) Threading the router changes call-site behavior today → observable change on Claude Code. (c) The router is allowed to override the fixed verifier pin. (d) A routing field named the bare `host` collides with `agent_sessions.host = socket.gethostname()` (`register.py:402`). (e) Later enabling dynamic routing requires call-site edits (defeats the whole Phase-0 point). (f) `iteration_loop.py` gets touched.

**File paths & call sites touched.**
- `bin/_host/registry.py` (new; `register_transport(family, transport)`/`get_transport(family)->ModelTransport` — a plain dict; `"claude"` the only registrant).
- `bin/_host/routing.py` (new; frozen `RouteQuery`(role, intent, intent_kind, subjects, slug, `required_capabilities: frozenset[str]`) + `RouteDecision`(family, rule_id, reason); `TransportRouter` ABC; `StaticRouter` returning `RouteDecision(family="claude", rule_id="static-default")`; two-stage contract: eligibility filter `required_capabilities <= transport.capabilities()` THEN rule pick).
- `bin/_host/transport.py` (**editable**; add abstract `capabilities()->frozenset[str]` to `ModelTransport` and `ClaudeTransport`'s concrete set — `structured-output` is a real discriminator per reconciliation 1).
- `bin/_planner/two_call.py` (`_default_client()` at 273 gains `router: TransportRouter = StaticRouter()`; builds `RouteQuery(role=PLANNER, slug=…)`; derives the client from the routed transport).
- `bin/_qa/invoke.py` (`_default_client()` at 269 gains the same optional param; `RouteQuery(role=QA)`).
- `bin/_retry_loop/opus_adapter.py` (`build_adapters` at 114 gains `router: TransportRouter = StaticRouter()`; builds per-role `RouteQuery` for CODER/REVIEWER/VERIFIER; derives `spawn_*_fn` from `routed_transport.spawn_agent`).
- `bin/_chain_overnight/phase_spawn.py` (`build_adapters(...)` call at 428 — passthrough of the default router).
- `bin/_retry_loop/main.py` (`build_adapters(...)` calls at 529 and 692 — passthrough).
- **NOT touched:** `bin/_retry_loop/iteration_loop.py`.
- `tests/host_conformance/test_registry.py` (new).
- `tests/host_conformance/test_routing_static.py` (new).

**tests_enabled.** `tests/host_conformance/test_registry.py`, `tests/host_conformance/test_routing_static.py`

**test_plan.**
- `test_registry_roundtrip` — `register_transport`/`get_transport` round-trip; duplicate-family and unknown-family rejection.
- `test_static_router_all_roles` — `StaticRouter.route()` returns `family="claude"`, `rule_id="static-default"` for every `ModelRole`.
- `test_eligibility_filter` — the two-stage contract: a `RouteQuery` whose `required_capabilities` exceed `ClaudeTransport.capabilities()` is filtered out as a genuine subset check.
- `test_seam_behavior_parity` — under the default `StaticRouter`, each of the three Tier-A seams behaves identically to pre-change (planner client, qa client, and the `build_adapters` trio) — plus a stub-router redirect proof that a non-default router changes the selected transport with **zero call-site edits**.
- `test_no_field_named_host` — a static assertion that `RouteQuery`/`RouteDecision` expose no field named `host`.
- Guard: assert `iteration_loop.py` is not in the diff (documented as a review check).

**Dependencies.** SC1, SC4, SC5.

**Notes.** Editable. Router selects family only; the family's `ModelCatalog` + `OVERNIGHT_*` pins resolve the model within family, and the router cannot reach the verifier pin.

---

## SC7 — Skill routing metadata in `skills/*/SKILL.md` frontmatter

**Failure modes addressed.** (a) A custom top-level frontmatter key (e.g. `splock-routing:`) trips `claude plugin validate . --strict` and breaks CI. (b) Rolling the block out to all 11 skills at once, then discovering `metadata:` fails `--strict` — a large blast radius. (c) `requires`/family values drift from the governed capability enum / registered families. (d) Dynamic subject→family rules leak into per-unit frontmatter (they belong in the deferred `routing_rules.json`).

**File paths & call sites touched.**
- `skills/*/SKILL.md` (**SEALED — sanctioned edit path**; all 11: `code, develop-plan, implplan, plan, qa, qna, recon, research, review, test, wrap`; add the Agent Skills `metadata:` string-map: `splock-route-intent` (defaults to skill name), `splock-route-default-family: "claude"`, `splock-route-allowed-families: "claude"`, `splock-route-requires` (capability tags), `splock-route-subject-affinity` (optional); space-separated lists per the `allowed-tools` idiom). **Rollout: ONE skill first**, run the `--strict` smoke battery, only then the remaining ten.
- `tests/host_conformance/test_skill_metadata.py` (new).

**tests_enabled.** `tests/host_conformance/test_skill_metadata.py`

**test_plan.**
- `test_skill_metadata_governed` — parse every `SKILL.md`: assert each `splock-route-requires` tag is in the governed closed capability enum (the same one `capabilities()` draws from in SC6) and each family value is a registered transport family; `splock-route-intent` present (defaulting to skill name).
- `test_no_custom_keys_on_commands_agents` — assert no `splock-route-*` top-level keys were added to `commands/*.md` or `agents/*.md`.
- **Procedural `--strict` gate (narrative):** after the one-skill edit, run `pytest tests/test_smoke_battery.py -k strict` (the `claude plugin validate . --strict` gate at `test_smoke_battery.py:148`) before rolling out the rest; the CI-union enforcement of this gate is owned by SC10 (which lists `test_smoke_battery.py`). Record in the doc/PR why the Agent Skills `compatibility` field was NOT used (prose 1–500 chars, not machine-matchable).

**Dependencies.** SC6 (needs the governed capability enum + registered families to validate against).

**Notes.** Sealed `skills/**`. The one-skill-first discipline converts the medium-confidence "metadata survives `--strict`" assumption into an explicit empirical check.

---

## SC8 — Intent-registry routing fields (additive; JSONL + markers only)

**Failure modes addressed.** (a) A new field named `host` collides with `register.py:402`'s `socket.gethostname()`. (b) `subjects` accepts free text and a typo'd subject silently mis-routes later. (c) The additive fields are written to the DB/RDS in Phase 0 (out of scope; belongs to the recon-G5 migration). (d) `KIND` gets overloaded with routing granularity it shouldn't carry.

**File paths & call sites touched.**
- `bin/_intent/register.py` (**editable**; add `subjects` (JSON array, nullable/default empty) to the JSONL row (~164) and marker payload (~521); add nullable `model_family`/`routing_rule_id` keys to both; validate `subjects` against a governed vocabulary knob; explicitly assert no field is named `host`).
- `bin/intent` (**editable** CLI script; add the `--subjects cybersecurity,frontend` argument, threading into `register.run(...)` at ~260).
- `bin/_intent/refusal.py` and/or `bin/_intent/settings.py` (**editable**; a governed subjects vocabulary — a closed set or a `routing.subjects` settings knob per the three-layer resolver — which may start empty; `KIND` left untouched).
- `tests/host_conformance/test_intent_routing_fields.py` (new).

**tests_enabled.** `tests/host_conformance/test_intent_routing_fields.py`

**test_plan.**
- `test_subjects_written_to_jsonl_and_marker` — `--subjects cybersecurity,frontend` writes the array to both the JSONL row and the marker payload; omission → default empty.
- `test_subjects_vocabulary_rejection` — when a vocabulary is configured, an unknown subject is rejected (closed-enum discipline); empty vocabulary permits (start-empty behavior).
- `test_forensics_fields_nullable_roundtrip` — `model_family`/`routing_rule_id` round-trip when set and are absent/null when unset; when populated, `model_family` validates against the SC6 registry.
- `test_no_field_named_host` — negative assertion over the row and marker payload shape.
- Regression: `tests/test_intent_sqlite_backend.py` stays green (SC10 union) — no DB column added.

**Dependencies.** None (independent of `bin/_host/` ports per skeleton). Soft coupling: `model_family` validation references the SC6 registry only when populated — note it, but keep `depends_on: []`.

**Notes.** Editable `bin/`; the JSONL/marker targets under `.plugin-data/**` are written via the `bin/intent` CLI (the sanctioned writer), not by direct file edits. MySQL/RDS columns deferred to the recon-G5 slug (recorded in SC9).

---

## SC9 — Reservations, transport inventory & deferrals (documentation)

**Failure modes addressed.** A future phase re-invents a name already spoken for and collides — the `routing.*` settings namespace, the `routing_rules_v1.schema.json` filename and its recorded design decisions, the `intent.routed` EVENT member, the `agent_assignment.model_family` bump, the G5 RDS migration, the G4 `validate_packaging` port, the four-seam transport inventory, the no-import policy, or the stickiness semantics. Also: the "model after, never import" policy being forgotten and a routing library (with the LiteLLM import-cost + CVE-2026-42208 supply-chain risk) getting pulled in later.

**File paths & call sites touched.**
- `docs/plans/portability_scaffolding/portability_scaffolding_reservations.md` (**new; NOT sealed** — the sealed globs match `*_plan.md`/`*_orchestrator.md`, not `*_reservations.md`). Optionally append a one-line pointer to `docs/MULTI_ROUTING_ROADMAP.md` (editable). No source code.
- Content = the nine reserved surfaces (a)–(i) exactly as enumerated in the SC9 criterion: (a) `routing.*` settings namespace + `routing_rules.json` overlay; (b) `schemas/routing_rules_v1.schema.json` + its recorded decisions (missing-key = evaluate-false/skip never error; match fields implicit-AND only; optional reserved `fallback_families` ranked tuple, opt-in; `as_of` ISO-date staleness field; hard ban on numeric prices/quality scores in rules); (c) `intent.routed` reserved in the EVENT enum (naming only); (d) the post-Phase-2 `orchestrator_v1.schema.json::tasks[].agent_assignment.model_family` bump; (e) recon-G5 RDS `claude_session_id`/`parent_claude_session_id` migration; (f) recon-G4 `HostTooling.validate_packaging()` fourth port (`sdk_spawners.py:545` `claude plugin validate` seam); (g) four-seam transport inventory incl. `bin/_fleet/spawn.py:101` `build_child_argv` (reconciliation 3, Phase 5); (h) the "model after, never import" policy with the CVE-2026-42208 rationale; (i) stickiness semantics (route once per phase/spawn, family held sticky) + the open `RouteDecision.sticky_scope` question.

**tests_enabled.** *(empty — pure documentation deliverable)*

**test_plan.**
- `verification_kind: artifact_review` — reviewed for completeness against the SC9 checklist: all nine reserved items (a)–(i) present with names matching the substrate verbatim, no implementation code introduced, filenames/enum members spelled exactly. (asserts: nine-item presence + name-exactness; fixture: the reservations doc.)

**Dependencies.** None.

**Notes.** Editable doc target. This is the sole task carrying the `verification_kind` exemption; its `tests_enabled` is legitimately empty.

---

## SC10 — Conformance suite + zero-behavior-change / CI-green acceptance gate

**Failure modes addressed.** (a) The individual ports pass in isolation but the union isn't collectable / doesn't run as one gate. (b) A landed change silently regresses the standard Claude Code path (the global invariant). (c) The affirmative proofs (deny-parity, sanitize round-trips, catalog contract, nested-scrub) aren't wired as blocking gates for their SCs. (d) The smoke battery `--strict` gate regresses after the SC7 skill-metadata rollout.

**File paths & call sites touched.**
- `tests/host_conformance/conftest.py` (new; suite collection/fixtures — the only substantive new write).
- `tests/host_conformance/__init__.py` (ensured present from SC1).
- The 13 conformance test files (listed here **only** to satisfy the `tests_enabled` selector-path contract; SC10 runs them as the union and must NOT weaken any upstream assertion): `test_events.py`, `test_shim_claude.py`, `test_deny_parity.py`, `test_transcript_claude.py`, `test_transport_claude.py`, `test_sanitize_schema.py`, `test_catalog_claude.py`, `test_roster_schema.py`, `test_registry.py`, `test_routing_static.py`, `test_intent_routing_fields.py`, `test_skill_metadata.py`, `test_nested_session_scrub.py`.
- `tests/test_smoke_battery.py` (existing; listed to gate the `--strict` union; SC10 does not edit its assertions).

**tests_enabled.** (the full union)
`tests/host_conformance/test_events.py`, `tests/host_conformance/test_shim_claude.py`, `tests/host_conformance/test_deny_parity.py`, `tests/host_conformance/test_transcript_claude.py`, `tests/host_conformance/test_transport_claude.py`, `tests/host_conformance/test_sanitize_schema.py`, `tests/host_conformance/test_catalog_claude.py`, `tests/host_conformance/test_roster_schema.py`, `tests/host_conformance/test_registry.py`, `tests/host_conformance/test_routing_static.py`, `tests/host_conformance/test_intent_routing_fields.py`, `tests/host_conformance/test_skill_metadata.py`, `tests/host_conformance/test_nested_session_scrub.py`, `tests/test_smoke_battery.py`

**test_plan.**
- `test_suite_collectable` — the `tests/host_conformance/` package collects and runs as one selector union; no import errors.
- Gating relationships recorded: `test_deny_parity` gates the SC2 `hook-entry` cutover; `test_sanitize_schema` gates SC4; `test_catalog_*` (verifier-independence) gates SC5; `test_nested_session_scrub` is a mandatory SC4 affirmative proof that must pass before the transport port is "landed."
- Zero-behavior-change assertion: the whole union plus `test_smoke_battery.py` is green — the standard Claude Code path is unchanged.
- **Guardrail (narrative):** SC10 must not modify the assertions of `test_smoke_battery.py` or any upstream conformance test; `file_paths_touched` lists them only because the `tests_enabled` contract requires each selector's path to appear in the same task, and `depends_on` (SC1–SC8) guarantees they already exist and pass individually.

**Dependencies.** SC1, SC2, SC3, SC4, SC5, SC6, SC7, SC8. (SC9 is doc-only and intentionally not a gate dependency.)

**Notes.** Editable. This is the acceptance-gate task; the file-overlap with earlier tasks is intentional and sequentially safe.

---

# Summary of the task graph for Call 2

| Task | Depends on | New source | Sealed edits | `tests_enabled` (files) | Special |
|---|---|---|---|---|---|
| SC1 | — | `bin/_host/events.py` | none | `test_events.py` | root vocabulary |
| SC2 | SC1 | `bin/_host/shim.py`, `bin/hook-entry` | none (bin/ cutover only) | `test_shim_claude.py`, `test_deny_parity.py` | deny-parity gate |
| SC3 | SC1 | `bin/_host/transcript.py` | none | `test_transcript_claude.py` | fail-open |
| SC4 | SC1 | `bin/_host/transport.py` | none | `test_transport_claude.py`, `test_sanitize_schema.py`, `test_nested_session_scrub.py` | nested-scrub build-now |
| SC5 | SC4 | (extends transport.py) | `agents/_roster.json` (v4), `agents/verifier.md` (mirror) | `test_catalog_claude.py`, `test_roster_schema.py` | verifier invariant; ModelRole count |
| SC6 | SC1,SC4,SC5 | `bin/_host/registry.py`, `bin/_host/routing.py` | none | `test_registry.py`, `test_routing_static.py` | thread 3 DI seams; don't touch iteration_loop |
| SC7 | SC6 | — | `skills/*/SKILL.md` (11) | `test_skill_metadata.py` | one-skill-first `--strict` |
| SC8 | — | — | none (bin/ + CLI writer) | `test_intent_routing_fields.py` | no field named `host` |
| SC9 | — | `…_reservations.md` | none | *(empty)* | `verification_kind: artifact_review` |
| SC10 | SC1–SC8 | `tests/host_conformance/conftest.py` | none | full union + `test_smoke_battery.py` | acceptance gate |

Every task lands under the standing invariant: **zero observable Claude Code behavior change + CI green**. The only intended behavior delta anywhere is nested-session invocations going deadlock→success (SC4) — a strict capability gain. `bin/_retry_loop/iteration_loop.py` is never touched (already host-agnostic via injected `spawn_*_fn`/`run_verify_fn`). All Codex/Antigravity adapters, the `RuleRouter`, the `routing_rules_v1` engine, the RDS migration, and the `agent_assignment.model_family` bump remain named-only reservations per the non-goals.