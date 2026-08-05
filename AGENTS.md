# Splock Agent Guidelines

Rules, context, and environment guidelines for agentic assistants working in
the `splock` repository.

> **Scope note.** This file is **orientation, not enforcement.** Per
> `docs/VISION.md` §4.1, prose cannot enforce a boundary — anything written for
> a model to read can be reinterpreted by it. The rules below are load-bearing
> because the *hooks and CLI exit codes* behind them are, not because they are
> written here. Where a rule has a deterministic backstop, it is named.

## Project Overview

- **Name**: splock — *specification lock*
- **Language**: Python (standard library at runtime; `jsonschema` optional)
- **Environment**: SDK-backed flows (`anthropic`, `claude-agent-sdk`)
- **What it is**: a governed plan → implement → verify lifecycle for agentic
  coding, with a deterministic enforcement spine.

## Core Directives

### 1. `docs/VISION.md` is the first-principles reference

**Every plan, prompt, schema, hook, and code change must be traceable to a
clause in it.**

> **If the work you are about to do cannot be traced to `docs/VISION.md`, it is
> drift — stop and ask the operator.** Extending that document is a human
> decision, not an agent decision.

Read it before planning anything. Cite the section you are working under when
the work is non-obvious.

### 2. The two founding claims

Everything else is a consequence of these (VISION §4.1–§4.2, SPEC §0):

1. **Prose cannot enforce a boundary.** Enforcement lives only in code that
   executes outside the model — lifecycle hooks and CLI exit codes. Never treat
   an instruction file (including this one) as a guarantee.
2. **Self-certification is not verification.** The agent that does the work
   never decides whether the work is done. The verifier model is pinned and is
   not tunable.

### 3. Check what is built before claiming it exists

`docs/VISION.md` states **intent**. It is not evidence that any subsystem
exists. **`docs/IMPLEMENTATION_STATUS.md` is the repo-facts record** — consult
it before asserting a capability is available, and update it in the same commit
as work that changes a row.

### 4. Deferred work is routed, never dropped — and routing is not an escape

VISION §9 governs this, and it is the directive most easily abused:

- **The default destination for anything found inside a slug is that slug.**
  Routing work *out* is the exception, not the relief valve.
- **"Tedious", "outside my task", and "that would be a big change" are not
  reasons to defer.** They describe the slug's own backlog.
- A **marker** is *blocked work with a named trigger* — a real prerequisite
  outside the slug's control. A marker whose prerequisite cannot be named is
  abandoned work wearing a better label.
- An **outstanding item** is *unplannable work with an honest admission*. If you
  could write a task for it today, it is not one.
- Work that is merely later or larger goes to **tier-promote** — its own slug.

Route with `bin/route_issue`; mint markers with `bin/marker`. Never hand-edit a
ledger.

### 5. State is mutated only through a CLI

Plan substrate, orchestrator state, per-slug fleet state, and the intent journal
are **sealed** (VISION §4.4). Use `bin/plan --amend`, `bin/fleet`,
`bin/update_orchestrator`, `bin/intent` — never a raw edit. Raw edits are denied
by `PreToolUse` hooks, and the denial message names the sanctioned path.

### 6. Never edit splock's own substrate

`agents/**`, `commands/**`, `hooks/**`, and `skills/**` are sealed against every
agent, including splock's own (VISION §4.10). The prompts you run on and the
hooks that constrain you are exactly what you must not rewrite. If a change
there is genuinely needed, surface it to the operator.

### 7. Fail closed, and never pass a gate you could not evaluate

A gate that could not actually run is **not** a pass (VISION §4.7). Do not
report green on a pre-exhausted run, an empty orchestrator, or a skipped suite.
**Vacuous green is a defect class of its own.**

### 8. Public-artifact hygiene

No personal identity, no host identity, no private-repo provenance anywhere in
the tree. Enforced at zero by `tests/trace_grep.sh` — run it before committing.

## Environment Setup

- Python dependencies: `requirements-sdk.txt`
- Hygiene gate: `bash tests/trace_grep.sh`
- Tests: `pytest` (187 test files; `tests/acceptance/` is the lettered
  acceptance suite)

## Where to look

| Doc | What's in it |
|---|---|
| `docs/VISION.md` | First-principles reference — intent, invariants, drift guards |
| `docs/IMPLEMENTATION_STATUS.md` | Repo facts — what is built, partial, and not built |
| `DESIGN.md` | Architecture and design rationale |
| `docs/SPEC_v2.7.md` | Framework design specification |
| `ADOPTION.md` | Config surface, `SPLOCK_*` env interface |
| `docs/FLEET.md` | Multi-slug tracker, C&C, spawn/board/resume |
| `docs/FOLLOW_ONS.md` · `docs/outstanding_issues.md` | Named deferred scope · field bugs (the ledger is sealed — append via `bin/route_issue --type outstanding`) |
