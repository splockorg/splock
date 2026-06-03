# splock framework specification — v2.7 (sanitized)

> This is a sanitized, repo-agnostic restatement of the framework design
> specification that splock implements. It is written in our own words and
> carries no host-specific identity. It describes the *shipped* v1 surface of
> splock; scope that was deliberately left out of v1 is routed to named
> follow-ons in `docs/FOLLOW_ONS.md` rather than left as dangling work here.
>
> Section letters (A, D, F, G, I, J, P, …) are the framework's internal design
> sections; they are preserved as stable anchors so code comments and this spec
> agree, but their meaning is summarized here without reference to any private
> source document.

---

## §0. Thesis

The framework exists to make an LLM coding agent's work **governed** and its
guardrails **deterministic**. Two claims drive every design decision:

1. **Prose cannot enforce a boundary.** Anything written for the model to read
   — a system prompt, a skill, an agent contract, a file the agent retrieves —
   can be reinterpreted by the model or overridden by adversarial content the
   model ingests. Enforcement therefore lives only in code that executes
   outside the model: lifecycle hooks and CLI exit codes.
2. **Self-certification is not verification.** An agent that both does the work
   and judges whether it is done has no independent check. The framework
   separates the two roles and pins the judge.

Everything below is an application of these two claims.

---

## §A. Driver & chain context

A *chain* is a sequence of tasks executed against a plan. The driver owns all
state transitions; subagents never mutate state directly. At spawn time the
driver injects a small, typed context into the environment so that hooks can
make chain-aware decisions:

- a chain identifier and a per-session identifier,
- the active plan slug,
- the current phase number.

These are validated against fixed formats (an ISO-8601-stamped chain id, a
slug pattern, a bounded phase integer). The driver also runs a pre-spawn scan
for orphaned state and reconciles it before starting work, so a crashed prior
run cannot corrupt a fresh one.

## §D. Roster & model policy

The framework ships a fixed roster of subagents, each with a single
responsibility: **planner, coder, reviewer, verifier, recon, research, qa,
qna**. Model selection is policy, not improvisation:

- planner, reviewer, and coder models are documented defaults with environment
  overrides (`OVERNIGHT_CHAIN_PLANNER_MODEL`, `OVERNIGHT_SONNET_REVIEW_MODEL`,
  `OVERNIGHT_OPUS_CODER_MODEL`);
- the **verifier model is a required pin**, fixed in the verifier agent's
  frontmatter and intentionally not adopter-tunable. The completion gate's
  independence rests on the judge being a fixed, dated model the executor cannot
  steer.

Model pins are plain values; the framework is deliberately Anthropic-native and
does not abstract behind a provider interface.

## §F. The completion gate (retry loop)

The completion gate is the loop that runs a task's coder and refuses
self-certification:

1. The coder writes code within the task's declared file scope and runs the
   task's enabled test set through a POSIX test wrapper.
2. On failure, the coder iterates, bounded by a retry cap (`OVERNIGHT_TEST_MAX_RETRIES`,
   default 3).
3. An independent verifier — on the pinned model — judges readiness. Only a
   READY verdict, predicated on a green run, advances the task.

Tampering tripwires run alongside: editing a test file, matching a suppression
pattern, or touching a sealed path is detected by a hook and recorded, so a run
made "green" by gutting a test does not pass. A debug toggle
(`OVERNIGHT_DEBUG_RETRY_PROMPT`) can surface the retry prompt for diagnosis.

## §G. The hook enforcement spine

Hooks are declared in `hooks/hooks.json` and fire on Claude Code tool-use and
session-lifecycle events. They are the primary enforcement altitude; a refusal
is a non-zero exit code from the hook.

- **PreToolUse:** a security dispatcher; first-edit intent registration; a
  lazy-dump output cap; a suppression-pattern block; a sealed-state delete
  block; package-safety and safe-DDL refusals; an eval-gate pre-commit check.
- **PostToolUse:** test-at-edit surfacing; a test-file-edit flag; plan
  Markdown-twin re-render.
- **Session lifecycle:** SessionStart (shell envelope + bookkeeping), Stop
  (verify-on-stop), SubagentStop, UserPromptSubmit, SessionEnd.

Defense in depth: the sealed-path inventory is enforced both as JSON-deny hooks
and as settings-level deny rules (`hooks/permissions.deny`), so a known
edit-tool gotcha that slips past one layer is caught by the other.

### Sealed paths

A closed-list inventory (`hooks/sealed_paths.txt`) names the path globs an agent
may not write or delete: per-slug plan state, chain session/lock files,
orchestrator state and its log, the plan/orchestrator substrates and their
rendered twins, baselines, regression cases, the intent registry journal, and
project secrets. The inventory is portable — it is expressed relative to the
adopter's project root.

## §I. Configuration & the env-var registry

All configuration resolves with a fixed precedence: **environment variable >
`.splock.toml` > built-in default**. Every environment variable any chain code
path reads is enumerated in a single typed registry
(`bin/_env_inventory/registry.py`): name, type, default, value constraints,
propagation class, and consuming call sites. Consumers import the name constant
and resolve through the registry rather than reading the environment ad hoc; a
test enforces that no bare environment read escapes the registry. The registry
validates itself against a JSON Schema at import, and an unknown schema version
is refused loudly.

## §J. Evaluation gate

The framework can gate commits on an evaluation/regression signal. Baselines
are minted and stored as schema-validated manifests; an eval-gate pre-commit
hook compares against a strict threshold (`EVAL_GATE_STRICT_THRESHOLD`) and can
be explicitly, loudly overridden for a single action
(`EVAL_GATE_OVERRIDE` + a required `EVAL_GATE_OVERRIDE_REASON`). Failure
artifacts are captured and garbage-collected on a retention window
(`EVAL_FAILURE_RETENTION_DAYS`). Regression cases can be replayed deterministically.

## §P. Intent & collision registry

Before its first edit, a session registers the area it intends to touch. The
registry detects overlapping claims between sessions and, by default, **halts**
on collision — preventing parallel agents from clobbering each other. The
action is configurable (`SPLOCK_INTENT_COLLISION_HALT_ACTION`: halt / warn /
log_only). Three interchangeable backends exist
(`SPLOCK_INTENT_BACKEND`: sqlite / jsonl / mysql); SQLite is the
zero-dependency default. The registry journal is itself a sealed path and is
append-only.

---

## §S. State, schemas, and the plan-of-record

- **State is mutated only through a CLI.** Orchestrator and plan state are JSON
  files changed via `bin/update_orchestrator` and the plan amend path, which
  validate against schema, append to the log, and enforce legal transitions.
- **The plan substrate is sealed.** `<slug>_plan.json` cannot be raw-edited;
  surgical changes go through `bin/plan --amend`, wholesale changes through
  `--reopen`, and the Markdown twin is always re-rendered in lockstep so the
  human-readable plan can never drift from the substrate.
- **Every artifact has a schema.** `schemas/` holds the JSON Schemas for the
  plan, plan patch, orchestrator, orchestrator log, per-slug state, markers,
  failures, lessons, regression cases, baseline manifests, score emissions,
  spans, and the env inventory. Validation prefers `jsonschema` and falls back
  to a hand-rolled check, so the tooling has no hard third-party dependency.

---

## §E. Path & data resolution

The framework distinguishes a read-only install root (`CLAUDE_PLUGIN_ROOT`,
for shipped assets) from a persistent data root (`CLAUDE_PLUGIN_DATA`, for all
mutable state). The data root falls back to the project directory and then the
repo root when running in-tree, and is created on first resolution. The
canonical resolver is `bin/_env_paths`; shell hooks reference the roots
directly. Full semantics are in `docs/PLUGIN_ENV_CONTRACT.md`.

---

## §X. Portability & platform

POSIX only (Linux, macOS, WSL2). Hooks and `bin/` wrappers are `bash`; Python
tooling is standard-library at runtime. The minimum Claude Code CLI version is
pinned in `docs/CLI_VERSION.md`, and CI must pin the exact CLI version rather
than tracking latest.

---

## §Z. Deferred scope

The framework's full design corpus includes work intentionally left out of the
shipped v1 plugin — most notably a privileged, human-only plumbing-admin
surface for modifying splock's own hooks and sealed paths. These are not
dangling TODOs in this spec; each is recorded as a named follow-on in
`docs/FOLLOW_ONS.md` with its scope and gating condition.
