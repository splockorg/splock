# Amendment reasoning — `portability_scaffolding` plan substrate (SC4-focused amend)

This is an **amend** of the already-schema-valid `<slug>_plan.json`. The operator directive lands two verified changes on **SC4** (Model Transport port). My job in Call 1 is to reason through exactly how the substrate should change so Call 2 emits a coherent, still-valid plan. The directive is narrow (SC4), but a clean amend must thread the change through the three other places the substrate mentions the affected surfaces: **SC10** (the conformance suite that holds SC4's tests), the **transport conceptual-architecture component**, and the **problem_statement** narrative (which enumerates the reconciliations). Everything else is unchanged, and I say so per-SC so the DAG and non-goals are confirmed intact.

Two framing decisions up front that govern the whole amend:

- **Both changes are strictly additive to SC4.** No task boundary moves, no new cross-SC dependency appears, and the `tasks_skeleton` DAG stays valid (SC4 still depends on SC1; SC5/SC6 still depend on SC4; SC10 still depends on all). The amend is a *content* change to SC4/SC10 (+ two consistency edits), not a structural replan.
- **Neither change violates the guardrails.** "Wrap-don't-rewrite ClaudeTransport" survives because the env-scrub is a new isolated guard at the spawn boundary (a sibling of the existing `_force_subscription_auth`), not a rewrite of the model-call logic; and `iteration_loop.py` is still not touched (the scrub lands in `_sdk_bridge` + `sdk_spawners`, both already in-scope for SC4). The "build-now sanitize helper" is a pure function, not a live CodexTransport, so CodexTransport stays a non-goal.

## SC1 — Host-neutral hook vocabulary (`bin/_host/events.py`)

**Unchanged by this amend.** The directive touches only the transport spawn boundary and the sanitize helper; the frozen hook vocabulary has no relationship to either. No edit. Its role as the shared substrate SC4 depends on is preserved.

## SC2 — Hook Shim port + `hook-entry` + trampoline cutover

**Unchanged.** The env-scrub is a *model-transport* spawn concern, not a hook-dialect concern; the deny-parity gate and the shim contract are untouched. No edit.

## SC3 — Transcript Provider port + `hook_writer` refactor

**Unchanged.** Transcript scraping does not spawn a nested `claude` and does not sanitize schemas. No edit. (Note for coherence: the RDS "host session id" flag in SC3 is unrelated to the "host env var scrub" here — different meanings of "host"; no collision to reconcile.)

## SC4 — Model Transport port + ClaudeTransport (**both amendments land here**)

This is the substantive amend. Two additions, both operator-confirmed 2026-07-22.

### Amendment A — nested-session env scrub at the spawn boundary (new hard requirement)

**Failure mode addressed.** The dominant failure is a **total-availability deadlock**: when `bin/plan` or any SDK-spawning splock command (`/plan`, `/qa`, `/implplan`, the retry-loop reviewer) is invoked *from inside* a Claude Code session, the nested `claude` CLI inherits the parent's session-identity markers and the `claude_agent_sdk` query hangs indefinitely. Empirically confirmed: a minimal query hangs with the vars set and completes (exit 0) with them scrubbed. The SDK bridge has **no nested-session guard today** — so this is a real, currently-broken Phase-0 seam, not a hypothetical portability reservation. The directive is explicit that this is an SC4 requirement, not a deferral.

**Exact scrub set (authoritative, 8 vars).** The criterion must record the precise denylist so the implementer doesn't guess:
`CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_CHILD_SESSION`, `CLAUDE_CODE_BRIDGE_SESSION_ID`, `CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_CODE_EXECPATH`, `CLAUDE_PID`, `CLAUDE_EFFORT`.

**Critical design constraints to encode in the criterion text** (these are the correctness traps a naive implementation would hit):

1. **Explicit denylist, never a `CLAUDE_*` prefix wipe.** A broad prefix wipe would also strip `CLAUDE_PLUGIN_ROOT` / `CLAUDE_PLUGIN_DATA` / `CLAUDE_PROJECT_DIR`, which the child *legitimately* needs (they are the host-neutral env contract, `bin/_env_paths/__init__.py:51-53`). The scrub MUST be exactly these eight, kept as a **named maintained constant** (e.g. `NESTED_SESSION_ENV_VARS`) so a future marker is a one-line addition and the conformance test asserts against the same constant.
2. **No-op when unset → preserves the global zero-behavior-change gate.** In the normal (non-nested) spawn path none of the eight vars are present, so the scrub changes nothing observable. The *only* behavior change is nested invocations going deadlock→success — a strict capability gain that regresses no previously-working path. This is how Amendment A reconciles with SC10's "zero observable Claude Code behavior change" gate; the criterion should state the no-op-when-unset property as an acceptance property.
3. **One shared helper, applied at every `claude`-subprocess spawn boundary.** The deadlock hits both the `complete`/`stream` path (via `_sdk_bridge.SubscriptionClient`'s query launch) and the `spawn_agent` path (the retry-loop reviewer via `sdk_spawners`). So the scrub belongs in a single pure helper invoked at (a) the SubscriptionClient query launch in `bin/_sdk_bridge.py`, sitting **alongside `_force_subscription_auth` (`_sdk_bridge.py:286-306`)** as a second spawn-boundary env guard, and (b) `ClaudeTransport.spawn_agent`'s effective-env construction before it delegates to `sdk_spawners`. `iteration_loop.py` stays untouched — `sdk_spawners` is a separate module and is already SC4's territory.
4. **Wrap-don't-rewrite compliance.** Adding a guard adjacent to `_force_subscription_auth` (which already manipulates spawn env) is the sanctioned "wrap" idiom, not a rewrite. This should be stated so the implementer doesn't over-refactor `_sdk_bridge`.

**Test that catches the failure mode.** A regression/conformance test that:
- sets all eight vars in a parent env, drives the transport spawn path, and asserts the **effective child env excludes all eight** — captured via the existing **injectable `query_fn`/`options_cls` seam** (`_sdk_bridge.py:496-561`) and a captured-env stub for the `spawn_agent` path, **not** by launching a real `claude` subprocess (a live spawn would risk re-introducing the very hang in CI);
- asserts a scrubbed-env spawn **does not inherit a parent `CLAUDE_CODE_SESSION_ID`** (the directive's explicit acceptance);
- asserts the **over-scrub guard**: `CLAUDE_PLUGIN_ROOT` / `CLAUDE_PLUGIN_DATA` / `CLAUDE_PROJECT_DIR` survive the scrub;
- asserts the **no-op-when-unset** property (an env without the eight is byte-identical after scrub).

This test lives under `tests/host_conformance/` — I recommend a dedicated `test_nested_session_scrub.py` (the concern is spawn-boundary env hygiene, distinct from transport delegation), and it must be added to SC10's selector union (see SC10 below). It is deterministic, fast, and hermetic (no network, no subprocess).

### Amendment B — resolve the sanitize_schema hedge toward BUILD-NOW

**Failure mode addressed.** The current SC4 text carries an *ambiguity hazard*: "Scope reading (a tested pure schema-transform helper now, NOT a CodexTransport…) is **flagged for operator confirmation; safe fallback is a golden-fixture spec test even if the helper is not wired**." That hedge lets an implementer legitimately *not* build the helper, which would (a) leave the P0-proven transform to rot as an unexecuted appendix, and (b) desynchronize SC4 from SC10 — SC10 already assumes the CLI transform ships and is pinned by `test_sanitize_schema.py` against a canned gpt-5.6 emission validated against the ORIGINAL strict schema. The hedge is the only place in the substrate that contradicts SC10.

**Resolution.** Strike the "flagged for operator confirmation / safe fallback" sentence entirely. Replace with a firm build-now statement: the **pure, transport-agnostic CLI-dialect `sanitize_schema` helper is in scope for Phase 0** (operator-confirmed 2026-07-22) — it ships as a tested pure function with the exact steps already enumerated in SC4 (strip `$schema`/`$id`/`$comment`; recursively `required = all properties` + `additionalProperties:false`; `const→single-value enum`; drop `minLength`/`maxLength`/`pattern`/`minItems`/`maxItems`/`format`; keep `enum`; then validate the emission against the ORIGINAL strict schema), and is **pinned by the SC10 golden-fixture conformance test**. The one preserved boundary: **CodexTransport itself stays deferred** — the helper is *not* wired into any live transport in Phase 0; it is a reference function the future Codex adapter phase will consume. This keeps the non-goal "Codex/Antigravity adapters" intact while removing the hedge on the helper.

**Test.** No new test needed — SC10 item (3) already covers it (`test_sanitize_schema.py`: byte-stable Claude `strip_schema_meta_keys` round-trip **plus** the proven CLI transform against golden schemas including `plan_v1.schema.json`, with the gpt-5.6 emission validated against the strict original). The amend simply makes SC4 promise what SC10 already tests. Reconciliation 5 in the problem_statement is the empirical basis and stays.

### SC4 dependencies (unchanged)

SC4 still depends on SC1 (`events.py` vocabulary). Neither amendment introduces a new upstream dependency: the env-scrub is internal to the ClaudeTransport wrap of `_sdk_bridge`/`sdk_spawners`; the sanitize resolution is internal to the transport module + its SC10 test. SC5 (catalog) and SC6 (registry/routing) still depend on SC4 unchanged.

### Proposed SC4 verification-line update

Add the nested-session-scrub test to SC4's "Verified by" list: `tests/host_conformance/test_transport_claude.py`, `test_sanitize_schema.py`, **and `test_nested_session_scrub.py`**; existing planner/qa/retry-loop tests stay green.

## SC5 — ModelCatalog + roster schema_version 4 + verifier invariant

**Unchanged.** The env-scrub and sanitize helper are transport-plumbing concerns; role→pin resolution and the fixed-verifier contract are orthogonal. No edit. (Consistency check: the scrub does not touch any model-pin resolution, so the `OVERNIGHT_*` precedence and the fixed verifier pin are unaffected.)

## SC6 — Transport registry + routing seam (StaticRouter) + Tier-A DI

**Unchanged.** StaticRouter still selects family only; the spawn-boundary env scrub happens *below* the router, inside the chosen transport's spawn path, so the two-stage route contract and the DI threading are unaffected. No edit. Worth stating for coherence: because the scrub is inside `ClaudeTransport`, any future routed transport inherits the same discipline by contract without router changes.

## SC7 — Skill routing metadata in `skills/*/SKILL.md`

**Unchanged.** No relationship to the transport spawn boundary or schema sanitize. No edit.

## SC8 — Intent-registry routing fields

**Unchanged.** Additive JSONL/marker fields; independent of the transport. No edit.

## SC9 — Reservations, transport inventory & deferrals (documentation)

**Effectively unchanged, with one coherence note.** The env-scrub is now an *implemented SC4 requirement*, not a reservation, so it does **not** belong in SC9's reservation set — the directive is explicit ("a real Phase-0 seam-hardening item… not a reservation"). The four-seam transport inventory in SC9 (item g) stays as-is: it inventories where model-invocation spawns happen (`SubscriptionClient`, the two `_default_client()` Protocols, `sdk_spawners`, and `bin/_fleet/spawn.py:104`), and those are exactly the boundaries the scrub must cover if/when their ports land. Optionally, SC9's inventory prose may add a one-line cross-reference noting that the SC4 nested-session env scrub applies at each live `claude`-subprocess spawn boundary — a documentation nicety, not a scope change. `verification_kind: artifact_review` exemption stays.

## SC10 — Conformance suite + zero-behavior-change / CI-green gate

**Amended for consistency with SC4 (Amendment A).**

**What changes:** the suite gains the nested-session env-scrub regression test as a first-class member, and its selector joins the enumerated union. Concretely, SC10's selector union — currently `test_events.py, test_shim_claude.py, test_deny_parity.py, test_transcript_claude.py, test_transport_claude.py, test_sanitize_schema.py, test_catalog_claude.py, test_roster_schema.py, test_registry.py, test_routing_static.py, test_intent_routing_fields.py, test_skill_metadata.py` plus `tests/test_smoke_battery.py` — should add **`test_nested_session_scrub.py`**.

**Failure modes SC10 must now also guard:**
- *Incomplete scrub* (misses one of the eight vars → deadlock persists): caught by asserting the child env excludes **all eight** against the `NESTED_SESSION_ENV_VARS` constant.
- *Over-scrub* (strips `CLAUDE_PLUGIN_*` → breaks the child's env contract): caught by the survival assertion.
- *Behavior drift on the non-nested path* (scrub not a no-op → violates the global gate): caught by the no-op-when-unset assertion. This is the item that keeps SC10's "zero observable Claude Code behavior change" gate honest for the standard path.

**Gating relationships (unchanged, plus one):** deny-parity still gates SC2 cutover; sanitize round-trips still gate SC4; the catalog contract still gates SC5. Add: the nested-session-scrub test is part of SC4's affirmative regression proof (it must pass before the transport port is considered landed). Amendment B needs no SC10 change — item (3) already pins the CLI transform; the amend only removes SC4's contradicting hedge, bringing SC4 into agreement with SC10.

## Cross-cutting substrate edits (for Call 2 coherence)

Beyond the two SC criteria, the amend should keep two narrative/architecture fields self-consistent:

**`conceptual_architecture` → transport component** (`bin/_host/transport.py (Model Transport port + ClaudeTransport)`): the purpose text should (a) add the nested-session env scrub alongside "keeps `_force_subscription_auth` internal" — e.g. "…scrubs the eight Claude Code session-identity env vars at the `claude`-subprocess spawn boundary (nested-session deadlock guard, no-op when unset), and keeps `_force_subscription_auth` internal"; and (b) it already says "ships the proven pure CLI-dialect sanitize transform," which is now unambiguously correct — no change needed there, and no deferral language to remove in this component (the hedge lived only in SC4's text). The `tests/host_conformance/` component purpose may add the env-scrub regression test to its enumerated fixtures.

**`problem_statement`:** it enumerates five reconciliations; I recommend adding a **sixth** recording the new empirical finding and the hedge resolution, e.g.: "(6) the nested-session deadlock is empirically confirmed (2026-07-22): SDK-spawning splock commands hang when invoked inside a Claude Code session because the nested `claude` inherits eight session-identity env vars — so ClaudeTransport/`_sdk_bridge` MUST scrub `CLAUDECODE, CLAUDE_CODE_SESSION_ID, CLAUDE_CODE_CHILD_SESSION, CLAUDE_CODE_BRIDGE_SESSION_ID, CLAUDE_CODE_ENTRYPOINT, CLAUDE_CODE_EXECPATH, CLAUDE_PID, CLAUDE_EFFORT` at the spawn boundary (a build-now SC4 item, not a reservation; no-op when unset, preserving `CLAUDE_PLUGIN_*`); and the CLI-dialect `sanitize_schema` helper ships now as a tested pure function (the earlier operator-confirmation hedge is resolved to build-now), while CodexTransport stays deferred." This keeps the substrate's own narrative aligned with the amended SC4.

**`non_goals`:** no change. "Codex/Antigravity transport… adapters" remains a non-goal (the shipped sanitize helper is a pure function, not a transport); nothing in non_goals references the env scrub.

## Summary of the amend

- **SC4:** add the eight-var nested-session env-scrub requirement (named constant, no-op-when-unset, over-scrub guard, shared helper across `_sdk_bridge` + `sdk_spawners`, `iteration_loop.py` untouched, tested via the injectable spawn seam) **and** remove the sanitize-helper "operator confirmation / safe fallback" hedge, resolving to build-now (helper ships and is pinned; CodexTransport stays deferred).
- **SC10:** add `test_nested_session_scrub.py` to the suite union; the scrub test guards incomplete-scrub, over-scrub, and non-nested no-op; SC4's sanitize is already pinned by the existing `test_sanitize_schema.py`.
- **Consistency edits:** transport conceptual component gains the scrub in its purpose; problem_statement gains reconciliation (6); non_goals and the SC1–SC3, SC5–SC9 criteria and the whole `tasks_skeleton` DAG are unchanged.

The plan remains schema-valid and internally consistent, with SC4 and SC10 now in agreement and the empirical basis recorded.