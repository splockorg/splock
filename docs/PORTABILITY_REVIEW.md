# splock Portability Review — Codex CLI & Google Antigravity

*Feasibility review, 2026-07-15. Scope: what it takes to run splock's governed plan → implement → verify lifecycle on OpenAI Codex CLI (GPT-5.5/5.6 era) and Google Antigravity (2.0 / Antigravity CLI), alongside Claude Code.*

## 1. Verdict

**Feasible on both, with moderate effort.** Both target platforms now expose the three primitives splock's enforcement model depends on: lifecycle hooks with a block/deny contract, markdown-defined skills/subagents, and a headless programmatic mode with JSON-schema-constrained output. splock's correctness-critical logic already lives in stdlib Python CLIs and shell scripts behind JSON schemas — that core is platform-agnostic. What must be re-authored per platform is the *adapter shell*: hook wiring, transcript parsing, packaging, and the model transport.

Estimated split: ~70% of the codebase (state model, schemas, retry-loop orchestration, intent registry, sealed-path/suppression logic, rubrics, eval tooling) ports as-is; ~30% is per-platform adapter work.

## 2. What splock assumes today (Claude-specific coupling)

From the codebase audit:

| Coupling | Where | Severity |
|---|---|---|
| CC hook events + stdin-JSON/exit-code contract | `hooks/hooks.json`, ~20 scripts | High — full rewiring per platform, logic reusable |
| Transcript scraping of `~/.claude/projects/*.jsonl` | `bin/_intent/hook_writer.py`, `bin/_hooks/session_start_hook.py` | High — regexes against CC's internal jsonl schema |
| Model transport via `claude_agent_sdk` → `claude` CLI | `bin/_sdk_bridge.py`, `bin/_retry_loop/sdk_spawners.py` | Medium — already behind Protocol/injection seams |
| Commands/skills/agents packaging | `commands/*.md`, `skills/`, `agents/`, `.claude-plugin/` | Medium — mostly frontmatter translation |
| Hardcoded model IDs incl. non-overridable verifier pin (`claude-haiku-4-5`) | `two_call.py`, `sdk_spawners.py`, `agents/verifier.md` | Medium — design assumption, needs per-family equivalents |
| `CLAUDE_*` env contract | `bin/_env_paths/` | Low — resolver is generic, values swap |
| CLI schema quirk (2020-12 meta-key strip) | `_sdk_bridge.py::strip_schema_meta_keys` | Low — each platform has its own schema-dialect quirks |

The three existing seams a new backend plugs into:
- `bin/_sdk_bridge.py::SubscriptionClient` — `.messages.create/.stream` surface with injected `query_fn`/`options_cls`.
- `bin/_planner/two_call.py::AnthropicClient` Protocol + `_default_client()` factory (same pattern in `bin/_qa/invoke.py`).
- `bin/_retry_loop/sdk_spawners.py::ClaudeAgentSDKClient` Protocol — spawners are injected into `iteration_loop.run_test_step_loop`, so the Ralph gate itself is transport-agnostic.

## 3. Target platform capabilities (researched July 2026)

### OpenAI Codex CLI
- **Hooks**: `hooks.json` or `[hooks]` in `config.toml` (`~/.codex/` and `<repo>/.codex/`). Events: SessionStart, SubagentStart, PreToolUse, PermissionRequest, PostToolUse, PreCompact, PostCompact, UserPromptSubmit, SubagentStop, Stop. stdin JSON in; block via `{"decision":"block"}` / `permissionDecision:"deny"` / exit 2 + stderr. Only `type:"command"` handlers execute today. Caveat: not all shell calls are intercepted — only "simple" ones; some tool paths bypass hooks entirely (a real risk for sealed-path enforcement).
- **Skills/plugins**: skills are the authoring primitive, plugins (`plugin.json` manifest, marketplace-installable) the distribution primitive — a close analog of splock's CC marketplace model. Custom prompts deprecated in favor of skills. `AGENTS.md` for project instructions.
- **Subagents**: GA since v0.115.0, up to 6 concurrent.
- **Headless**: `codex exec` with `--output-schema` (JSON-schema-constrained final output) and `--json` JSONL event streaming with token usage — directly covers the two-call planner's Call-2 and the reviewer's rubric-bound output. TypeScript Codex SDK also supports `output_schema`.
- **Sessions**: rollout JSONL under `~/.codex/sessions`; Codex even ships an importer for Claude Code sessions/skills/config — evidence the formats are translatable.
- **Models**: GPT-5.5 default (400K context); 5.6-generation models rolling out.

### Google Antigravity (2.0 + Antigravity CLI)
- Gemini CLI transitioned into Antigravity CLI; it retains Agent Skills, Hooks, Subagents, Extensions (now "Antigravity plugins"), and shares one agent harness with the IDE.
- **Hooks**: JSON at `~/.gemini/antigravity-cli/hooks.json` (global) and `<project>/.agents/hooks.json` (workspace). Events consolidated to five: PreToolUse, PostToolUse, PreInvocation, PostInvocation, Stop. stdin JSON includes `session_id`, `transcript_path` (!), `cwd`, `toolCall.args`. Decision contract differs from CC/Codex: **always exit 0** and return `{"allow_tool": false, "deny_reason": "..."}` — non-zero exit means hook *failure*, not deny. Global+workspace veto composition.
- **Skills**: `~/.gemini/antigravity-cli/skills/` and `.agents/skills/*.md` — a markdown file becomes a slash command.
- **Subagents**: markdown + YAML frontmatter, or `agent.json` (`systemPromptSections`, `toolNames`); concurrent/background, plus scheduled tasks.
- **Headless/SDK**: Antigravity CLI supports terminal-native invocation and an SDK (2.0, I/O 2026); Gemini API supports `responseSchema` structured output.
- Fewer/coarser hook events than CC/Codex: no PostToolUse-per-edit granularity difference, but critically **no SessionStart/UserPromptSubmit/SubagentStop** — session-start context injection and subagent-stop accounting need PreInvocation/PostInvocation equivalents or CLI-side wrappers.

## 4. Proposed architecture

**Hexagonal split: one splock core, three host adapters.**

1. **Core (unchanged, stdlib Python)** — schemas, plan/orchestrator state machine, retry-loop `iteration_loop`, rubrics, intent registry, sealed-path/suppression/eval logic, `SPLOCK_*` config.
2. **Host adapter interface** (new, small):
   - *Hook shim*: one thin per-platform translator script that normalizes the host's stdin payload into a common internal event (`{event, tool, args, session, cwd}`) and translates the core's allow/deny verdict back into the host's decision dialect (CC/Codex: exit-2/deny JSON; Antigravity: exit-0 + `allow_tool:false`). The existing ~20 hook scripts call the core through this shim instead of assuming CC's contract.
   - *Transcript provider*: replace jsonl regex-scraping with per-platform providers behind one interface. Antigravity hands you `transcript_path` in the hook payload (easier than CC); Codex rollout JSONL under `~/.codex/sessions` needs its own parser. Longer term, prefer deriving intent/session facts from hook payloads (which all three deliver) over transcript scraping — it's the most fragile coupling today.
   - *Model transport*: implement `CodexClient` (wrap `codex exec --output-schema` / Codex SDK) and `AntigravityClient` (Antigravity SDK / Gemini `responseSchema`) matching the `SubscriptionClient` `.messages.create/.stream` + `_AdaptedMessage` surface, and per-platform spawner sets injected into the retry loop. Each transport owns its schema-dialect sanitizer (the 2020-12 strip generalizes: OpenAI structured outputs require `additionalProperties:false` + all-required; Gemini `responseSchema` is an OpenAPI-flavored subset).
3. **Packaging generators**: keep `commands/skills/agents` markdown as the single source, generate per-host artifacts at build time — CC plugin, Codex `plugin.json` bundle, Antigravity `.agents/` plugin. Frontmatter is the main translation (model pins, tool lists).

**Model-pin policy per family** (verifier independence must survive the port): verifier stays a small, fixed, non-overridable model per family — e.g. `claude-haiku-4-5` / a GPT-5-mini-class pin / a Gemini Flash-class pin — with coder/reviewer tiers mapped analogously and overridable via the existing `OVERNIGHT_*` envs.

**Suggested phasing**
1. **Phase 0 — seam hardening (in-tree, no new hosts)**: formalize the hook shim + transcript-provider interfaces; move CC specifics behind them; CI keeps CC green. Delete direct `~/.claude/projects` regexes from core.
2. **Phase 1 — Codex adapter** (closest sibling: near-identical hook events, plugin marketplace, `--output-schema` headless mode, even a CC-import path). Port the Ralph gate first — it's the highest-value, best-seamed subsystem.
3. **Phase 2 — Antigravity adapter**: hook shim for the 5-event/exit-0 dialect; emulate missing SessionStart/UserPromptSubmit via PreInvocation; validate the overnight chain against the Antigravity SDK.
4. **Cross-family conformance suite**: one golden test set (hook deny scenarios, constrained-emission round-trips, verifier READY gating) run against all three adapters.

## 5. Summary table — how splock differs across agent families

| Dimension | Claude Code (today) | OpenAI Codex CLI | Google Antigravity |
|---|---|---|---|
| Packaging | CC plugin via self-hosted marketplace (`.claude-plugin/`) | Plugin (`plugin.json`) + skills, marketplace-installable | Antigravity plugin; skills in `.agents/skills/` |
| Commands/skills | `commands/*.md` + `skills/*/SKILL.md` | Skills (custom prompts deprecated); `AGENTS.md` for project context | Markdown skill file → slash command |
| Subagents | `agents/*.md` + Task tool; roster JSON | Native subagents (GA, ≤6 concurrent) | Markdown+frontmatter or `agent.json`; background + scheduled |
| Hook config | `hooks/hooks.json` (plugin) | `~/.codex/` & `.codex/` `hooks.json`/`config.toml` | `~/.gemini/antigravity-cli/hooks.json` & `.agents/hooks.json` |
| Hook events splock needs | Pre/PostToolUse, SessionStart, Stop, SubagentStop, UserPromptSubmit — all present | All present (plus PermissionRequest, Pre/PostCompact) | Only 5 events; SessionStart/UserPromptSubmit/SubagentStop must be emulated via Pre/PostInvocation |
| Deny contract | exit 2 / permission deny JSON | `{"decision":"block"}` or exit 2 + stderr | **exit 0** + `{"allow_tool": false}` (non-zero = hook failure) |
| Hook coverage risk | Full tool interception | Some shell/tool paths bypass hooks — sealed-path gaps need settings-level backstop | Global-vs-workspace veto helps org enforcement |
| Headless transport | `claude_agent_sdk` → `claude` CLI (`_sdk_bridge`) | `codex exec` / Codex SDK | Antigravity SDK / CLI |
| Constrained emission | `output_config` json_schema (strip `$schema`/`$id`) | `--output-schema` / `output_schema` (needs `additionalProperties:false`, all-required) | Gemini `responseSchema` (OpenAPI-subset dialect) |
| Transcript access | Scrape `~/.claude/projects/*.jsonl` (fragile) | Rollout JSONL `~/.codex/sessions` (+ `--json` event stream) | `transcript_path` delivered in hook payload (cleanest) |
| Model pins | Opus planner/QA, Sonnet reviewer, Haiku verifier (fixed) | GPT-5.5/5.6 planner-coder, mini-class fixed verifier | Gemini Pro-class planner/coder, Flash-class fixed verifier |
| Auth model | Subscription via local CLI (no metered key) | ChatGPT plan via CLI, or API key | Google account via CLI, or Gemini API key |

## 6. Key risks

1. **Hook bypass on Codex** — sealed-path enforcement is splock's safety spine; Codex documents that some tool paths skip interception. Mitigate with Codex sandbox/permission config as a second layer (analog of `hooks/permissions.deny`).
2. **Antigravity event gaps** — session-lifecycle features (session-start context, subagent accounting) need redesign around PreInvocation/PostInvocation; verify semantics empirically, docs are young and the ecosystem is still churning post-Gemini-CLI migration.
3. **Schema-dialect drift** — three constrained-output dialects; without a per-transport sanitizer + live round-trip tests, we recreate the 0.1.3 "passed mocks, failed live" incident on two more platforms.
4. **Verifier-independence semantics** — CC lets us pin a non-overridable subagent model in frontmatter; confirm each host can prevent operator retuning of the verifier, or enforce the pin in the spawner layer instead.
5. **Docs volatility** — both platforms shipped their extensibility surfaces in the last ~6 months; expect contract churn and gate each adapter behind the conformance suite.

## Sources

- [Codex hooks](https://developers.openai.com/codex/hooks) · [Codex CLI](https://developers.openai.com/codex/cli) · [Non-interactive mode](https://developers.openai.com/codex/noninteractive) · [Config reference](https://developers.openai.com/codex/config-reference) · [Agents SDK guide](https://developers.openai.com/codex/guides/agents-sdk) · [Codex changelog](https://developers.openai.com/codex/changelog?type=codex-cli)
- [Codex plugin system](https://codex.danielvaughan.com/2026/03/30/codex-cli-plugin-system/) · [Codex in 2026](https://codex.danielvaughan.com/2026/03/27/codex-cli-in-2026-whats-new/) · [Claude Code import](https://codex.danielvaughan.com/2026/05/13/codex-cli-agent-migration-system-import-claude-code-sessions-skills-config/) · [codex exec in CI](https://www.developersdigest.tech/blog/codex-exec-ci-headless-guide) · [Codex complete reference](https://www.codegateway.dev/en/blog/openai-codex-cli-complete-guide-2026)
- [Gemini CLI → Antigravity CLI transition](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/) · [Antigravity CLI features](https://antigravity.google/docs/cli-features) · [I/O 2026 feature deep dive](https://antigravity.google/blog/google-io-2026-feature-deep-dive) · [Antigravity 2.0 launch](https://www.marktechpost.com/2026/05/19/google-launches-antigravity-2-0-at-i-o-2026-a-standalone-agent-first-platform-with-cli-sdk-managed-execution-and-enterprise-support/)
- [Antigravity hooks developer guide](https://medium.com/google-cloud/a-developers-guide-to-agent-hooks-in-antigravity-cli-4c1440febd11) · [Migrating to Antigravity CLI](https://medium.com/google-cloud/migrating-to-antigravity-cli-a841c6964f37) · [Subagent format discussion](https://github.com/google-gemini/gemini-cli/discussions/27305) · [Antigravity skills setup](https://github.com/addyosmani/agent-skills/blob/main/docs/antigravity-setup.md)
