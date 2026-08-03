# Implementation status — vision vs. what is actually built

**As of 2026-07-28.** Companion to [`VISION.md`](VISION.md).

> **Why this doc exists:** the vision is the first-principles reference, and its
> job is to stop drift. The largest drift risk is an agent citing an
> aspirational sentence in it as evidence that a subsystem already exists. This
> file states repo facts only — no intent, no design. **Update it whenever a row
> changes state**, in the same commit as the work.

Section references (`§n`) point at `VISION.md`.

## Built

| Vision element | Where |
|---|---|
| Enforcement spine — hooks + CLI exit codes, dual-layer deny (§6, §4.1) | `hooks/` — 20 hook scripts + `hooks.json` + `permissions.deny` + `sealed_paths.txt`; `tests/acceptance/test_acceptance_J_permissions_deny_sealed_paths_symmetry.py` |
| Sealed-state, suppression, test-file-edit, package-safety, safe-DDL, lazy-dump hooks (§6) | `hooks/{chain-sealed-state-delete-block,chain-suppression-block,chain-test-file-edit-flag,package-safety,safe-ddl,lazy-dump-cap}.sh`; `tests/acceptance/test_acceptance_E_*` |
| Intent / collision registry, SQLite default (§6) | `bin/intent`, `bin/_intent/`; `tests/test_intent_sqlite_backend.py`, `test_acceptance_G_intent_collision_dispatch.py` |
| Two-call planner — reasoning call separated from schema-valid emission (§5) | `bin/plan`, `bin/_planner/`; `tests/acceptance/test_acceptance_IJ_two_call_planner_structural.py` |
| Sealed plan substrate + regenerated Markdown twin + amend path (§4.3, §8) | `bin/plan --amend`, `bin/_render_plan/`; `tests/test_planner_amend_rollback.py` |
| Completion gate — retry loop, bounded cap, tamper tripwires (§7) | `bin/_retry_loop/`; `tests/test_retry_loop_overnight_cap.py`, `test_acceptance_J_r4_tamper_flagged_logging.py` |
| Pinned verifier, not adopter-tunable (§4.2, §7) | `agents/verifier.md` frontmatter `model: claude-haiku-4-5-20251001`; enforced by `tests/test_verifier_model_pin_required.py` + `test_acceptance_B_verifier_model_pin.py` |
| Schema-or-refuse — 15 JSON Schemas, draft 2020-12 enforced (§8) | `schemas/`; `tests/acceptance/test_acceptance_H_all_schemas_draft_2020_12.py`, `test_acceptance_J_schemas_consumers_match.py` |
| Lifecycle surface — 11 slash commands, 9 subagents + roster (§5) | `commands/`, `agents/`; `tests/acceptance/test_acceptance_J_roster_schema_subagent_enum_identity.py`, `test_acceptance_I_recon_not_a_slash_command.py`, `tests/test_agent_twins_match_the_engine.py` |
| One vocabulary — qa / qna / eli5 disjoint (§5) | `bin/eli5`, `bin/_eli5/`, `commands/eli5.md`, `agents/eli5.md`; `tests/test_eli5/` |
| qna + recon database interrogation — server-level `mcp__mysql` grant; inert without adopter MCP config (§4.12); recon's only DB lane (no Bash) | `agents/qna.md` + `agents/recon.md` frontmatter + "MySQL MCP" sections; `ADOPTION.md` §3 "MySQL MCP for `/qna` and `/recon`" |
| mysql-mcp-guard — read-only gate on the MCP lane: PreToolUse statement filter (`mcp__mysql__.*`) + `SHOW GRANTS` credential probe as /qna spawn gate; write-capable exit 51, unverifiable exit 52 (fail closed §4.7); `SPLOCK_MYSQL_MCP_GUARD` halt/warn/off | `bin/mysql-mcp-guard`, `bin/_mysql_mcp_guard/`, `hooks/mysql-mcp-guard.sh`, `bin/_hooks/mysql_mcp_guard_hook.py`; `tests/test_mysql_mcp_guard.py` |
| fleet — per-slug state, generated hub zones, 11 subcommands (§10) | `bin/fleet`, `bin/_fleet/`; `tests/test_fleet_engine.py`, `test_fleet_close_and_zones.py` |
| Contention designed out — per-slug writes joined at render (§4.6) | `bin/_fleet/`; `tests/test_fleet_concurrency.py` |
| fleet C&C — `spawn` / `board` / `resume` on subscription CLI transport (§10) | `bin/_fleet/spawn.py`, `spawn_runner.py`, `board.py`; `tests/test_fleet_cnc.py` |
| Lifecycle-as-bookkeeping — stage auto-integration, inert until opted in (§10, §4.12) | `bin/fleet stage`, `bin/_fleet/auto.py`; `tests/test_fleet_auto_integration.py` |
| **Four-way issue routing + forced escalate — closed enum, refuses if none fits (§9)** | `bin/route_issue`, `bin/_route_issue/{rubric,fix_now,outstanding,marker_route,tier_promote,escalate}.py`; `tests/acceptance/test_acceptance_D_route_issue_five_bucket_dispatch.py`, `test_acceptance_J_route_issue_determinism.py` |
| **Escalation triggers — CLI/hook-detected, evaluated before the rubric (§9)** | `bin/_route_issue/triggers.py`, `hooks/escalation-trigger-precommit.sh`; `tests/acceptance/test_acceptance_E_escalation_trigger_fires.py` |
| **Scheduled markers — schema-bound rows, create/close/show, prefix registry (§9)** | `bin/marker`, `bin/_marker/`, `schemas/marker_v1.schema.json`, `hooks/marker-validate-pre-commit.sh`; `tests/acceptance/test_acceptance_D_marker_create_close_show.py`, `test_acceptance_D_marker_prefix_collision.py`, `test_acceptance_E_marker_validate_precommit.py` |
| **"Nothing may sit forever" — open-ended trigger refused at creation (§9)** | `tests/acceptance/test_acceptance_D_marker_refuses_open_ended_trigger.py` |
| **Outstanding ledger + lazy-dump cap on the append (§9)** | `bin/_route_issue/outstanding.py`, `bin/lazy-dump-check`, `hooks/lazy-dump-cap.sh`, `outstanding_issues.md` (OI-1…OI-3); `tests/acceptance/test_acceptance_D_lazy_dump_check_cap.py` |
| Morning-review queue, routing into markers + outstanding (§9, §11) | `bin/morning-review` (`route-marker`, `route-outstanding`, `reactivate`, `abandon`, `gc`); `tests/acceptance/test_acceptance_D_morning_review_subcommands.py` |
| Overnight chain driver — **single slug**, phases 2–5, wall-clock cap, pause/resume, completion summary on every exit (§11) | `bin/chain-overnight`, `bin/chain-pause`, `bin/chain-resume`, `bin/chain-status`, `bin/_chain_overnight/`; `tests/acceptance/test_acceptance_K_chain_*`, `test_acceptance_C_completion_summary_*` |
| Halt-on-`NEEDS_HUMAN` — the *halt* rung of §11's tolerance ladder, as the only behavior | `tests/acceptance/test_acceptance_C_verifier_needs_human_halt.py` |
| Evaluation gate, baselines, trend (§6) | `bin/eval-gate`, `bin/eval-baseline`, `bin/eval-trend`, `hooks/eval-gate-pre-commit.sh`; `tests/acceptance/test_acceptance_F_*` |
| Subscription transport, no metered key read (§4.11) | `bin/_fleet/spawn.py`, `bin/_sdk_bridge.py`; `tests/test_planner_subscription_transport.py` |
| Adopter path contract — install root vs. project root via one helper (§13) | `bin/_env_paths/`, `docs/PLUGIN_ENV_CONTRACT.md`; `tests/test_env_paths_project_root.py`, `test_wrapper_project_resolution.py`, `test_plugin_asset_resolution.py` |
| Zero-config + full env override surface (§4.12) | `.splock.toml`, `ADOPTION.md` §4; `tests/test_adopter_config_roundtrip.py` |
| Public-artifact hygiene grep gate at zero (§4.16) | `tests/trace_grep.sh`, `tests/test_trace_scrub.py` |
| Sole-writer-per-sealed-path discipline (§4.4, §8) | `tests/acceptance/test_acceptance_H_sole_writer_per_sealed_path.py`, `test_acceptance_J_atomic_write_discipline_all_writers.py` |
| Lessons, regression replay, span/log renderers | `bin/lessons`, `bin/regression-replay`, `bin/render_spans`, `bin/render_log`; `tests/test_lessons_cli.py`, `test_render_spans.py` |
| Standing agent directives, anchored on the vision (§4.1 — orientation, not enforcement) | `AGENTS.md` (repo root; read by Claude Code and Codex), `.agents/AGENTS.md` (pointer for the Antigravity workspace convention), linked from `README.md` |

Test surface backing the above: **187 test files** under `tests/`, including a
lettered acceptance suite (`tests/acceptance/`).

## Partially built

| Vision element | Status |
|---|---|
| **Receipts on both paths** (§4.8, §8) | **red path only.** A failing verdict leaves its artifact; a green verdict does not persist reviewer reasoning. This is issue **#57** and vision §16.1. The vision states the invariant; the tree does not yet satisfy it |
| **splock dogfooding its own deferred-work ledger** (§4.15, §9) | **machinery shipped, ledger empty.** `docs/plans/scheduled_markers/list.md` reads "No markers yet" and `prefix_registry.md` reads "No prefixes registered yet" — both are scaffold placeholders. `outstanding_issues.md` *is* in use (OI-1…OI-3). So §9 runs in the adopter repo it was built for, and splock has not yet minted a marker against itself |
| **`wrap` — invocation authority** (§5) | **ships as a CLI; whether it may have an agent surface is undecided.** `bin/wrap` is the external-content delimiter boundary over a closed `WrapKind` enum — the trust boundary for content entering the plan record, *not* a closeout stage. Competing resolutions: an agent surface, or a call-site audit ensuring the ingesting engine wraps and the agent never does. **`OI-4`**, vision §16.13 |
| **`close` — no agent surface** (§5) | **ships as a CLI; nothing drives it.** `bin/fleet close` performs the atomic terminal transition (final event + archive + meta reconcile + successor mint + one render) and is invoked by hand. Every other stage self-records its transitions; closeout is the exception. **`OI-5`**, vision §16.14. Unrelated to `OI-4` beyond both lacking a surface |
| **splock's own ledger is outside its own seal** (§4.4, §4.15) | **inconsistent.** `hooks/sealed_paths.txt` seals `docs/outstanding_issues.md`, so an adopter's ledger is `bin/route_issue`-only. splock keeps its own at repo root — matching no sealed glob — so an agent can raw-edit it here. **`OI-6`.** Not fixable by an agent: the fix edits `hooks/sealed_paths.txt`, and `hooks/**` is sealed (§4.10) |
| **Deferral doctrine** (§9) | **written, unenforced.** §9 now states the policy — slug is the default destination, marker requires a named prerequisite, outstanding requires genuine unplannability, volume is the signal. Nothing in the tooling tests a routing decision against it. The CLI enforces *shape* (open-ended trigger refused, `data_needed` required, cap on the ledger) but not *justification*: an agent can file a well-formed marker for lazy reasons and no gate will notice |
| **Marker minting discipline** (§9) | **rules not brought over.** The portable conventions (prefix = domain, same-commit registration, sequence numbers burned, closed entries retained, forward declaration, graduation to slug) are proven in the adopter repo's `prefix_registry.md`; splock's own registry is a scaffold placeholder carrying none of them. `hooks/marker-validate-pre-commit.sh` validates rows, not the taxonomy rules |
| **Multi-routing Phase 0** (§12) | **proven, not built.** The transport experiments passed on 2026-07-22 (`codex` CLI installed and subscription-billed; `agy -p` executes; the sanitize transform round-trips a plan substrate through GPT against the strict schema) — see `docs/MULTI_ROUTING_ROADMAP.md`. Zero lines of splock code changed as a result |

## Not built

| Vision element | Status |
|---|---|
| **Model routing, Tier A** (§12) | not built — `bin/_host/` does not exist. No transport registry, no `StaticRouter`/`RuleRouter`, no capability filter. Roadmap phases 1–4 are specified and unimplemented |
| **`routing_rules_v1` + role × subject key + forensic route records** (§12) | not built — no schema, no rules file, no `RouteDecision` for model routing anywhere in `bin/` |
| **Auto mode / pool-draw observation** (§12) | not built, and gated on an open question (§16.8): not every host CLI surfaces its own consumption. No dominant-model or spread config surface exists |
| **Model routing, Tier B harness port** (§12) | not built — `bin/_fleet/spawn.py` hardcodes a Claude child. Explicitly a stretch in the vision |
| **Grok and local open-source families** (§12) | not built — later-generation scope; no family registry to add them to yet |
| **Fleet-addressed investigation** (§10) | not built — `/recon`, `/research`, `/qna` take `<slug>` as a required argument (`commands/recon.md` `argument-hint: <slug> [free-text-tail]`). There is no fleet address, no slug-assignment step, no multi-slug split, and no route-to-§9 path for a finding that fits no slug |
| **Overnight kickoff question window** (§11) | not built — `bin/chain-overnight` starts driving phases immediately. No question-collection window, no operator answer-capture step |
| **Blocker-tolerance dial** (§11) | not built — the run halts on `NEEDS_HUMAN` and that is the only behavior. `--defer-threshold` / `--wall-clock-seconds` / `--test-max-retries` are test-retry and time caps, **not** an operator-facing tolerance setting. Neither *route-around* nor *decide-and-log* exists |
| **Fleet-scale overnight** (§11) | not built — `bin/chain-overnight` is single-slug (`starting_phase`, `slug`). No fleet-wide unattended driver |
| **FO-1 — `/splock` privileged plumbing-admin** (§16.6) | not built — design settled, brief build-ready (`docs/plans/splock_super_skill/SUPER_SKILL_BUILD_BRIEF.md`). Ship decision open |
| **FO-2 — non-pytest gate commands** | not built — see `docs/FOLLOW_ONS.md` |
| **FO-3 — operator console / dashboard** | not built by design; the vision excludes it from the plugin (§15). Any console is a downstream consumer of the JSON/CLI surface |
| **Agent teams / inter-agent messaging** (§12) | not built, and deliberately deferred behind Codex spawn support. The research conclusion is *do not build free-form messaging* |
| **Adopter-tunable escalation-trigger policy** (§16.4) | not built as a decision — one threshold is already an env knob (`ESCALATION_BLAST_RADIUS_FILES`), but which trigger parameters an adopter may tune is unsettled |
| **Everything-on-by-default adoption** (§13) | **not built — the current default is the opposite for fleet.** §13 now states an opt-out stance; today fleet is opt-in (`bin/fleet init` writes `_fleet_meta.json`, and `bin/fleet stage` is a silent no-op until then). Vision §16.15 holds the open question of whether a single-slug adopter should carry fleet state from install |
| **Disable switches** (§13) | not built — there is no config surface for turning off deferred-work routing, fleet, or restricting to a single model org. `.splock.toml` and the `SPLOCK_*` env set carry thresholds, paths, and model pins, not capability switches |
