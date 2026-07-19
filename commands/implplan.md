---
description: Implplan a splock slug — promotes plan substrate to orchestrator substrate via two-call planner
argument-hint: <slug> [free-text]
---

# /implplan — operator-direct implplan CLI entry

Triggered by the operator with: `/implplan $ARGUMENTS`

Where `$ARGUMENTS` is **one or more** tokens, space-separated:

- **One token** (`<slug>` only) — bare invocation; no directive, no reopen.
- **Two or more tokens** (`<slug> <free-text-tail>`) — the first token is
  the slug; the remainder is operator-authored prose that this command
  interprets as either an explicit flag (`--reopen`, `--directive "..."`),
  a redo-synonym (mapped to `--reopen`), or directive content (passed
  through to the CLI as `--directive`). See the `## Parse the operator's
  tail` section below.

This command runs the two-call planner against the slug's existing
`docs/plans/<slug>/<slug>_plan.json`. It produces
`<slug>_orchestrator.json` via `bin/implplan` (POSIX shell wrapper
around `python -m bin._planner.main implplan`).

## File-existence gate

Per plan §1.C slash-command quick-reference, the gate logic:

**REFUSE if:**

- `docs/plans/<slug>/<slug>_plan.json` does NOT exist (predecessor
  artifact missing — run `/plan <slug>` first).
- `docs/plans/<slug>/<slug>_orchestrator.json` DOES exist AND `--reopen`
  was NOT passed. Use `--reopen` to overwrite the prior artifact in
  place — the flag is the canonical bypass for this gate.

The non-prompt path's enforcement substrate is `bin/_planner/main.py::
_build_inputs` + `_output_target`, which raises a usage error
(exit code 8, `target_exists_no_reopen`) when the target exists and
`--reopen` was not set.

With `--reopen`, the target-exists gate is skipped and the prior
orchestrator JSON is overwritten unconditionally; a `Reopened:
overwrote <abs-path>` notice is emitted to stderr. Unlike `/plan`, the
`/implplan` command has NO downstream-cascade dependency (the
orchestrator IS the leaf), so `--reopen` is a simple bypass. Note that
re-running `/implplan --reopen` against a slug with live
`_orchestrator_log.jsonl` rows for in-progress tasks is a documented
sharp edge (see std_command_operator_extensions QA D.5 / Q3).

The slash-command's own bash precheck mirrors this structure: gate-skip
when `--reopen` is set, gate-check otherwise.

```bash
if [ -z "$REOPEN" ]; then
    # Gate enforces target-must-not-exist
    [ ! -f "docs/plans/$SLUG/${SLUG}_orchestrator.json" ] || { echo "REFUSE: target exists; use --reopen to overwrite"; exit 1; }
else
    # --reopen set: skip the target-exists check; proceed to CLI
    :
fi
```

## Parse the operator's tail

When `$ARGUMENTS` contains more than just the slug, the main-agent
interpretation layer maps the tail prose to closed CLI flags. The
substrate (`bin/_planner/main.py`) only accepts canonical flags; this
slash command translates operator-natural prose into those flags.

**Step 1 — extract slug.** First whitespace-separated token = slug. Rest
= tail (preserving internal whitespace; trim outer).

**Step 2 — detect explicit flags.** If the tail begins with `--reopen`
(as a literal token), strip it and set `reopen=true`. Any explicit
`--directive "..."` substring is honored verbatim.

**Step 3 — detect redo-synonyms in prose.** If the tail contains any of
the canonical redo-synonym vocabulary, set `reopen=true`:

- `redo`
- `restart`
- `resume`
- `regenerate`
- `from scratch`
- `retry`
- `redo from scratch`
- `open this again`
- `redo it`

Match case-insensitively. The dual-role rule: detect redo-synonyms →
emit `--reopen=true` AND pass full prose as `--directive`. The synonym
trigger does NOT consume the prose; the operator's full original tail
flows through as directive content so the subagent sees the operator's
full intent.

**Step 4 — pass remaining prose as directive.** After stripping any
literal flag tokens (e.g., a leading `--reopen` literal), the remaining
prose (trimmed) is the directive content. If non-empty, pass it to the
CLI as `--directive "<remaining>"`. The substrate enforces the 8KB UTF-8
cap; over-cap directives refuse with exit 1 (usage) before any SDK
call.

**Step 5 — surface the transparency notice.** When `--reopen` was
auto-extracted from prose (i.e., NOT from a literal `--reopen` token),
print a one-line operator-facing notice BEFORE running the CLI:

```
Interpreted '<original-tail-prose>' as --reopen + --directive '<remaining-prose>'
```

Mirrors the `code.md:71-74` "Auto-picked T<n>" notice format. The notice
exists so the operator can see in-conversation what the prose-extraction
layer decided.

## Invocation examples

Run from the repo root under WSL2 / Ubuntu.

**Bare slug** (first run; no directive; no reopen):

```bash
bin/implplan <slug>
# or with optional knobs:
bin/implplan <slug> --tier 'Tier 2' --repo-state 'main @ commit abc123'
```

**Explicit flags** (operator types the canonical CLI form):

```bash
bin/implplan <slug> --reopen --directive "shrink task count by collapsing TG1+TG2"
```

**Prose-driven** (operator types natural language; this slash command
interprets):

```
/implplan <slug> redo this from scratch and shrink the task count
```

→ The slash-command body interprets as: `--reopen` (from "redo … from
scratch") + `--directive "redo this from scratch and shrink the task
count"`, with a transparency notice surfaced first.

**Chain-driven invocation:**

```bash
bin/implplan <slug> --chain-id <chain-id> --remaining-budget-usd <usd>
```

## Two-call mechanism

Per plan §D.1: Call 1 (reasoning, no `response_format`) + Call 2
(emission, `response_format` set to `schemas/orchestrator_v1.schema.json`).
Same two-call structural enforcement as `/plan`.

Note: plan §D calls this the "implplan" step; the schema file is named
`orchestrator_v1` because §B's substrate uses the canonical filename
convention `<slug>_orchestrator.{json,md}` per cross-cutting line 236.

## Exit codes

Per implplan §A.impl.3a shared closed-enum:

- 0  = success (JSON written to `docs/plans/<slug>/<slug>_orchestrator.json`)
- 1  = usage error (slug dir missing, `<slug>_plan.json` missing,
       directive over 8KB cap, etc.)
- 5  = budget below floor (`OVERNIGHT_PLANNER_MIN_BUDGET_USD`)
- 7  = atomic write failed
- 8  = target_exists_no_reopen (orchestrator.json present and `--reopen`
       not set; use `--reopen` to overwrite)
- 16 = SDK retry exhausted — operator edits schema or re-does recon /
       research and re-runs

## Fleet auto-tracking (opt-in)

No command-level calls needed: when the project has opted into the
fleet lifecycle tracker (`docs/plans/_fleet/_fleet_meta.json`
exists — see `docs/FLEET.md`), `bin/implplan` records `implplan` /
`✈️ wip` on start and `🕛 ready --next /code` on success engine-side
(`--stdout` runs are not tracked). On a project that has not opted in
this is a no-op.

## Cross-references

- `bin/implplan` — POSIX shell wrapper
- `bin/_planner/main.py` — Python CLI entry
- plan §1.C — slash-command quick-reference + file-existence gate +
  `--reopen` contract
- plan §D.1-§D.7 — two-call planner full rationale
- `.claude/agents/planner.md` — subagent contract
- `schemas/orchestrator_v1.schema.json` — emission schema
- `std_command_operator_extensions` slug (TA + TD) — `--reopen` and
  `--directive` substrate additions; this MD's TG1 prose-extraction
  layer
