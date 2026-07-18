# Adopting splock

This guide takes you from zero to a working splock install in an existing
project, then documents the complete configuration interface: the `.splock.toml`
config file and every environment variable splock reads.

---

## 1. Prerequisites

- **Claude Code CLI `>= 2.1.160`.** Check with `claude --version`. See
  `docs/CLI_VERSION.md` for the rationale and the CI pinning procedure.
- **A POSIX shell environment** — Linux, macOS, or WSL2. There is no
  Windows-native support; the hooks and `bin/` wrappers are `bash` scripts.
- **Python 3.10+** on `PATH` for the `bin/` tooling. Runtime is standard
  library; `jsonschema` is used when installed and gracefully skipped when not.

---

## 2. Install the plugin

splock is a self-hosted Claude Code marketplace whose single plugin is the repo
itself.

```text
# Inside Claude Code:
/plugin marketplace add splockorg/splock
/plugin install splock@splock
```

For development or evaluation, sideload directly from a checked-out tree:

```bash
claude --plugin-dir ./
```

Confirm the manifests are valid:

```bash
claude plugin validate . --strict
```

---

## 3. Configure your project

Configuration is **optional** — splock ships working defaults, and an absent
config file runs the framework as-is. To customize, copy `.splock.toml` from
this repo into your project root and edit it.

Resolution precedence, highest first:

```
process environment variable  >  .splock.toml  >  built-in default literal
```

So an environment variable always wins over the TOML file, which always wins
over the hard-coded default at the call site.

### `.splock.toml` keys

| Section / key | Meaning | Default | Env override |
|---|---|---|---|
| `[project] name` | Logical project name; used in log lines and as the default plan-slug home. | `"splock"` | — |
| `[project] venv_path` | Explicit virtualenv path. | unset → auto-detect | `SPLOCK_VENV` |
| `[intent] backend` | Intent/collision registry backend: `sqlite`, `jsonl`, or `mysql`. | `"sqlite"` | `SPLOCK_INTENT_BACKEND` |
| `[intent] collision_halt_action` | What to do on an overlapping path claim: `halt`, `warn`, or `log_only`. | `"halt"` | `SPLOCK_INTENT_COLLISION_HALT_ACTION` |
| `[intent.mysql] host/port/database` | MySQL connection — only read when `backend = "mysql"`. Secrets stay in `.env`, never here. | unset | (standard DB env) |
| `[models] planner_model` | Planner model pin. | `"claude-opus-4-8"` | `OVERNIGHT_CHAIN_PLANNER_MODEL` |
| `[models] reviewer_model` | Reviewer model pin. | `"sonnet"` | `OVERNIGHT_SONNET_REVIEW_MODEL` |
| `[models] coder_model` | Coder model pin. | `"opus"` | `OVERNIGHT_OPUS_CODER_MODEL` |
| `[templating.domain_example_placeholders]` | Map of placeholder token → your concrete example, substituted into generated prompts/docs so examples read in your domain. | empty | — |

> The **verifier** model is deliberately *not* in this table. It is a required
> pin set in `agents/verifier.md` frontmatter and is intentionally not
> adopter-tunable — the completion gate's independence depends on it.

### Secrets

splock does not require any secret to run on its default SQLite backend. If you
select the MySQL intent backend, put the database credentials in a local `.env`
file at your project root (it is git-ignored). **Do not commit secrets** and do
not put them in `.splock.toml`. There is intentionally no `.env.example`
shipped — the complete environment-variable interface is documented in
Section 4 below, which is the authoritative reference.

---

## 4. Environment-variable interface (complete `SPLOCK_*` reference)

Every variable splock reads is listed here with its meaning and default. They
fall into three groups: framework configuration you may set; chain-context
variables the driver sets for you; and the path roots the Claude Code host
provides.

### 4.1 Adopter-settable configuration

| Variable | Meaning | Default | Valid values |
|---|---|---|---|
| `SPLOCK_VENV` | Path to the virtualenv splock's wrappers and hooks should activate. Resolution order is: an already-active `$VIRTUAL_ENV` → `$SPLOCK_VENV` → `./.venv` → bare `python3` on `PATH`. | unset → `.venv` | a directory path |
| `SPLOCK_INTENT_BACKEND` | Intent/collision registry storage backend. | `sqlite` | `sqlite`, `jsonl`, `mysql` |
| `SPLOCK_INTENT_COLLISION_HALT_ACTION` | Behavior when two sessions claim overlapping paths. (The SessionStart hook forces `log_only` for its own bookkeeping regardless.) | `halt` | `halt`, `warn`, `log_only` |
| `SPLOCK_INTENT_AUTO_REGISTER_INTERACTIVE` | Whether an interactive (non-chain) session auto-registers its intent on first edit. | unset (off) | `1`, `true`, `yes`, `on` |
| `SPLOCK_INTENT_AREA` | Pre-set the intent "area" string, taking precedence over the `--area` CLI flag. | unset | free string |
| `SPLOCK_INTENT_SUMMARY` | Pre-set the intent summary string, taking precedence over the `--summary` CLI flag. | unset | free string |
| `SPLOCK_SEALED_PATHS_FILE` | Override the path to the sealed-paths inventory file. | unset → `hooks/sealed_paths.txt` | a file path |

### 4.2 Chain-context variables (set by the driver — you normally do not set these)

When splock runs a task chain, the driver injects context that the chain-scoped
hooks read. They are listed for completeness and for debugging; setting them by
hand is rarely correct.

| Variable | Meaning | Format |
|---|---|---|
| `SPLOCK_CHAIN_ID` | Identifier of the current chain run. | `chain_<ISO-8601-UTC>` |
| `SPLOCK_INTENT_SESSION_ID` | Identifier of the current session, used by the first-edit intent hook. | `sess_<ISO-8601-UTC>_<4 chars>` |
| `SPLOCK_PLAN_SLUG` | The plan slug the chain is operating on. | lowercase slug |
| `SPLOCK_PHASE` | The current phase number (2–5). | integer |

### 4.3 Model pins (framework-wide, not `SPLOCK_`-prefixed)

These are plain, documented defaults — environment overrides for the model used
at each role boundary. They are not behind a provider abstraction; splock is
Anthropic-native by design.

| Variable | Role | Default |
|---|---|---|
| `OVERNIGHT_CHAIN_PLANNER_MODEL` | Planner (two-call planning). | `claude-opus-4-7-20260517` |
| `OVERNIGHT_SONNET_REVIEW_MODEL` | Reviewer (phase-boundary review). | `claude-sonnet-4-6-20260101` |
| `OVERNIGHT_OPUS_CODER_MODEL` | Coder. | (Opus class) |
| `OVERNIGHT_VERIFIER_MODEL` | Verifier — **pinned; do not override.** | `claude-haiku-4-5-20251001` |

Other `OVERNIGHT_*` knobs (test retry caps, defer thresholds, orphan grace,
debug toggles, mode flags) and the cross-cutting hook knobs (`LAZY_DUMP_CAP`,
`PACKAGE_SAFETY_*`, `EVAL_GATE_*`, `GUARDRAIL_MODE`, `OPERATOR_OVERRIDE`) are
enumerated with their types, ranges, and consumers in the master registry at
`bin/_env_inventory/registry.py`. That registry is the single source of truth;
this guide surfaces the ones an adopter is most likely to touch.

### 4.4 Path roots (provided by the Claude Code host)

| Variable | Meaning |
|---|---|
| `CLAUDE_PLUGIN_ROOT` | Read-only install directory of the plugin. Used to resolve shipped assets. Falls back to the tree location when sideloaded. |
| `CLAUDE_PLUGIN_DATA` | Persistent per-plugin data directory. All mutable runtime state lives here (intent DB, JSONL mirror, sealed local state). Falls back to `CLAUDE_PROJECT_DIR`, then the repo root. |
| `CLAUDE_PROJECT_DIR` | The adopter's project root, used as the data-dir fallback. |

See `docs/PLUGIN_ENV_CONTRACT.md` for the full resolution semantics.

---

## 5. First run

A minimal end-to-end flow on a fresh slug:

```text
/recon  my-feature           # optional: survey first
/plan   my-feature           # author the plan substrate + Markdown twin
/implplan my-feature         # expand into the orchestrator task DAG
/code   my-feature           # execute tasks under the completion gate
```

`/code` runs each task's coder under the completion gate and will not advance a
task until the pinned verifier confirms a green test run.

Running several slugs at once? Opt into the fleet status hub
(`bin/fleet init`) and every stage run above starts tracking itself on a
generated, contention-free status board — see `docs/FLEET.md`.

---

## 6. Verifying your install (adoption smoke checks)

After installing, confirm the plugin is healthy:

```bash
# 1. Manifests validate strictly.
claude plugin validate . --strict

# 2. The Python tooling imports and the test suite is green.
python -m pytest tests/ -q

# 3. The host-trace scrub gate is clean (no provenance leaked in).
bash tests/trace_grep.sh
```

If you keep your virtualenv somewhere other than `./.venv`, export
`SPLOCK_VENV=/path/to/venv` first so the wrappers and hooks activate the right
interpreter.

**SDK-backed flows need two optional packages.** The core substrate is
stdlib-only, but the two-call planner (`bin/plan`, `bin/implplan`) imports
`anthropic`, and the test-step retry loop (`bin/verify test-step`) imports
`claude-agent-sdk` — both lazily, only when those flows run. Install them into
the venv splock activates:

```bash
pip install -r requirements-sdk.txt   # anthropic + claude-agent-sdk
```

Without them those specific commands fail with a clear ModuleNotFoundError /
`sdk_smoke_failed` naming the missing package; the rest of the substrate is
unaffected. The SDK-backed flows also need `ANTHROPIC_API_KEY` on the
environment (or in the repo-root `.env` that `load_env_file` reads).

A green run of all three is the adoption gate: the plugin loads, the engine
works, and the tree is clean.

---

## 7. Where to go next

- **[DESIGN.md](DESIGN.md)** — the architecture and the reasoning behind it.
- **[docs/SPEC_v2.7.md](docs/SPEC_v2.7.md)** — the framework design
  specification (sanitized, repo-agnostic).
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — how to contribute, including the DCO
  sign-off requirement.
