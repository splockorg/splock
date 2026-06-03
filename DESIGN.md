# splock — design

This document explains what splock is, the problem it solves, and how its
pieces fit together. It is written from the shipped tree, in our own words.

---

## 1. Problem statement

A coding agent that can edit files and run commands is powerful and unreliable
in the same breath. On a real codebase it will, given the chance:

- start writing code before there is any agreed plan;
- drift outside the scope it was given;
- modify files it has no business touching, including other agents' work;
- claim a task is finished without actually running the tests;
- and — most insidiously — weaken the guardrails meant to catch all of the
  above, because the guardrails were expressed as instructions the agent can
  reinterpret.

Instructions in prose ("run the tests", "don't touch sealed files", "stay in
your lane") are advisory. A model can rationalize past them, and any content
the model merely *reads* — a file, a tool result, a retrieved document — can
carry instructions that contradict them. Treating agent prose as an enforcement
mechanism is the original sin.

splock's design principle is therefore: **every decision that matters for
correctness or safety is made in deterministic code, outside the model.** The
model proposes; hooks and CLIs dispose.

---

## 2. The lifecycle

splock structures agent work as an explicit, reviewable pipeline:

```
recon / research / qna  →  plan  →  implplan  →  code (+ test)  →  review / qa  →  wrap
```

- **recon / research / qna** are exploratory: survey the problem, gather
  context, capture an interactive Q&A log. Their outputs are artifacts a plan
  can ingest.
- **plan** turns an initiative into a structured specification: a set of
  success criteria and a task skeleton, emitted as a schema-valid JSON
  substrate plus a rendered Markdown twin.
- **implplan** expands the plan into an *orchestrator*: a directed acyclic
  graph of tasks, each with dependencies, file scope, an enabled test set, and
  an agent assignment.
- **code** executes one task at a time under the completion gate (Section 4).
- **test** runs the enabled test set, either for a task or across a phase gate.
- **review / qa** provide a higher-altitude check at phase boundaries
  (review) and adversarial scrutiny of a plan or implementation (qa).
- **wrap** folds an exploratory artifact back into the durable plan record.

Each stage is a Claude Code slash command (`commands/*.md`), backed by a skill
(`skills/<name>/SKILL.md`) that carries the model-side procedure and, where a
deterministic engine exists, a `bin/` entry point that the skill routes
through.

### The plan is a sealed artifact

The emitted plan substrate (`docs/plans/<slug>/<slug>_plan.json`) is **sealed**:
it cannot be edited by a raw file write. Surgical changes go through a dedicated
amend path (`bin/plan --amend`) that applies a keyed patch and re-renders the
Markdown twin; wholesale changes go through `--reopen`. This guarantees the
plan of record and its human-readable rendering never silently diverge, and
that no agent can quietly rewrite the spec it is being held to.

### Two-call planning

The planner makes two distinct model calls. Call 1 is free-form reasoning —
the model thinks through the problem in prose. Call 2 is constrained to emit
JSON valid against `schemas/plan_v1.schema.json`. Separating reasoning from
structured emission keeps the substrate well-formed without flattening the
quality of the thinking that produced it.

---

## 3. The enforcement spine

The deterministic core is a set of Claude Code lifecycle hooks declared in
`hooks/hooks.json`, plus the CLI exit codes of the `bin/` tooling. Hooks fire on
tool-use events and can refuse an action by returning a non-zero exit code; the
model never sees a path around them because the decision happens in the host,
not in the conversation.

The hook surface:

- **PreToolUse** (before a tool runs):
  - a security dispatcher over `Edit | Write | Read | Bash | Task`;
  - the intent / first-edit registration hook;
  - a lazy-dump cap (bounds runaway output);
  - a suppression-pattern block (refuses edits that would silence tests or
    checks);
  - a sealed-state delete block over `Bash | Edit | Write`;
  - package-safety (refuses risky installs) and safe-DDL (refuses raw schema
    DDL from bash);
  - an eval-gate pre-commit check.
- **PostToolUse** (after an edit/write):
  - test-at-edit (surfaces the relevant tests when code changes);
  - a test-file-edit flag (notices when a test file itself is being edited);
  - plan-render-on-edit (keeps the plan Markdown twin in lockstep).
- **SessionStart / Stop / SubagentStop / UserPromptSubmit / SessionEnd** —
  session-lifecycle hooks that set up the shell envelope, run a verify-on-stop
  check, and emit session bookkeeping.

### Sealed paths

A closed inventory (`hooks/sealed_paths.txt`) lists the path globs that may not
be written or deleted by an agent: plan state (`_state.json`), the chain
session and lock files, orchestrator state and its log, the plan/orchestrator
substrates and their rendered twins, baselines, regression cases, and the
intent registry's local journal. The same inventory is mirrored as
settings-level deny rules (`hooks/permissions.deny`) for defense in depth, so a
known edit-tool gotcha that bypasses JSON-deny is still caught by the settings
layer. Project secrets (`.env`, `.env.*`) and the plugin's own substrate
(`.claude/agents/**`, `.claude/hooks/**`, `.claude/commands/**`) are sealed too.

### Intent / collision registry

Before an agent's first edit, it registers the area it intends to work in. Two
sessions that claim overlapping paths collide, and the default collision action
is to **halt** — preventing two agents (for example, parallel chain steps) from
silently clobbering each other's work. The registry has three interchangeable
backends selected by configuration:

- **SQLite** (default) — zero external dependency, a single file under the
  plugin data directory;
- **JSONL** — an append-only journal, for environments that prefer a
  plain-text audit trail;
- **MySQL** — for teams that already run a shared database.

### State is mutated only through a CLI

Orchestrator and plan state are JSON files. They are never edited in place by an
agent; mutations go through `bin/update_orchestrator` (and the plan amend path),
which validates against the relevant schema, appends to the log, and enforces
the legal state transitions. The state files are the single source of truth for
where a build is.

---

## 4. The completion gate

The heart of `/code` is a completion gate — a loop that refuses to let a coding
agent self-certify. For a given task:

1. The **coder** subagent writes code within the task's declared file scope and
   runs the task's enabled tests via a POSIX test wrapper.
2. The coder iterates on failures, up to a bounded retry cap.
3. A separate **verifier** subagent — running on a *pinned, dated model that
   the operator may not override* — independently judges whether the task is
   genuinely ready. The coder cannot declare the task done; only the verifier's
   READY answer (predicated on a green test run) advances it.

The pin matters: if the verifier could run on whatever model the executor
happened to be using, an executor could in principle steer its own judge. By
fixing the verifier model in the verifier's frontmatter and refusing to expose
it as an adopter knob, the gate's verdict stays independent of the work being
judged.

Tampering tripwires sit alongside the loop: if a step edits a test file, or
matches a suppression pattern, or tries to touch a sealed path, the hook fires
and the attempt is recorded — the gate does not quietly accept a "green" run
that was made green by gutting the test.

---

## 5. Path & data resolution

A plugin is installed read-only and may be relocated or refreshed by the host;
its mutable state must live somewhere durable. splock distinguishes two roots
(specified in `docs/PLUGIN_ENV_CONTRACT.md`):

- **`CLAUDE_PLUGIN_ROOT`** — the read-only install directory. Used to resolve
  shipped assets: `agents/`, `commands/`, `hooks/`, `bin/`, `schemas/`. With a
  fallback to the tree location when sideloaded.
- **`CLAUDE_PLUGIN_DATA`** — the persistent per-plugin data directory. All
  mutable runtime state lives here: the intent SQLite database, the JSONL
  mirror, sealed local state. With a fallback chain to the project directory and
  then the repo root when running in-tree.

The Python helper `bin/_env_paths` is the single resolver for both; shell hooks
reference the roots directly as `${CLAUDE_PLUGIN_ROOT}/...` /
`${CLAUDE_PLUGIN_DATA}/...` with the same fallback semantics. The directory is
created on first resolution so callers can write immediately.

---

## 6. Schemas

Every structured artifact is validated against a JSON Schema in `schemas/`:
the plan, the plan patch, the orchestrator and its log, the per-slug state,
markers, failures, lessons, regression cases, baseline manifests, score
emissions, spans, and the env-var inventory. Validation prefers the
`jsonschema` library when present and falls back to a hand-rolled structural
check when it is absent, so the tooling runs with zero hard third-party
dependency. Unknown schema versions are refused loudly rather than silently
accepted, so format drift cannot slip through.

---

## 7. Configuration model

splock is configured by a per-project `.splock.toml`, with an environment
variable able to shadow any key, and a built-in default at the call site as the
floor. Configuration is entirely optional — an absent `.splock.toml` runs the
framework on its defaults.

Precedence, highest first:

```
process environment variable  >  .splock.toml  >  built-in default literal
```

Model pins (planner, reviewer, coder) are plain, documented defaults exposed as
environment overrides — they are *not* hidden behind a provider-abstraction
layer, because the framework is deliberately Anthropic-native. The verifier
model is the one exception: it is a required pin, fixed in the verifier agent's
frontmatter, and intentionally not adopter-tunable (Section 4). The full
`SPLOCK_*` interface is enumerated in `ADOPTION.md`.

---

## 8. Portability

splock targets a POSIX environment (Linux, macOS, WSL2). Its hooks and `bin/`
wrappers are POSIX shell, and its Python tooling is standard-library at runtime.
It assumes the Claude Code CLI at or above the version pinned in
`docs/CLI_VERSION.md`. There is no Windows-native shell support in v1.

The framework was extracted from a private in-house automation system into this
standalone, repo-agnostic plugin. Every host-specific identity, path, and
domain token was scrubbed during extraction, and the trace-grep gate
(`tests/trace_grep.sh`) enforces that the published tree carries none of that
provenance.

---

## 9. What is intentionally out of scope (v1)

- **No provider abstraction.** splock is built for Claude Code and Anthropic
  models on purpose; it does not try to be model-agnostic.
- **No Windows-native shell.** POSIX only.
- **No GUI / dashboard.** The lifecycle is driven through Claude Code slash
  commands and the `bin/` CLIs; any operator console is a downstream concern,
  not part of this plugin.
- **A privileged plumbing-admin surface** (a human-only way to modify splock's
  own hooks and sealed paths) is a named follow-on, not part of v1 — see
  `docs/FOLLOW_ONS.md`.
