---
description: Plan a splock slug via the two-call planner — Call 1 free-form reasoning + Call 2 schema-valid JSON emission
argument-hint: <slug> [free-text]
---

# /plan — operator-direct planner CLI entry

Triggered by the operator with: `/plan $ARGUMENTS`

Where `$ARGUMENTS` is **one or more** tokens, space-separated:

- **One token** (`<slug>` only) — bare invocation; no directive, no reopen.
- **Two or more tokens** (`<slug> <free-text-tail>`) — the first token is
  the slug; the remainder is operator-authored prose that this command
  interprets as either an explicit flag (`--reopen`, `--amend`,
  `--directive "..."`), a redo-synonym (mapped to `--reopen`), an
  amend-synonym (mapped to `--amend`), or directive content (passed
  through to the CLI as `--directive`). See the `## Parse the operator's
  tail` section below.

This command runs the two-call planner against the slug's existing
`docs/plans/<slug>/` directory. It produces `<slug>_plan.json` via
`bin/plan` (POSIX shell wrapper around `python -m bin._planner.main plan`).

## File-existence gate

Per plan §1.C slash-command quick-reference, the gate logic:

**REFUSE if:**

- `docs/plans/<slug>/<slug>_recon.md` does NOT exist (predecessor artifact
  missing — run the recon subagent first).
- `docs/plans/<slug>/<slug>_plan.json` DOES exist AND `--reopen` was NOT
  passed. Use `--reopen` to overwrite the prior artifact in place — the
  flag is the canonical bypass for this gate.
- `docs/plans/<slug>/<slug>_orchestrator.json` DOES exist (cascade
  refusal). Even with `--reopen`, the planner refuses because reopening
  `<slug>_plan.json` would silently stale the downstream
  `<slug>_orchestrator.json`. To proceed, delete the orchestrator first
  or also reopen it via `/implplan <slug> --reopen`.

The non-prompt path's enforcement substrate is `bin/_planner/main.py::
_build_inputs` + `_output_target`, which raises a usage error
(exit code 8, `target_exists_no_reopen`) when these conditions are
violated.

With `--reopen`, the target-exists gate is skipped and the prior plan
JSON is overwritten unconditionally; a `Reopened: overwrote <abs-path>`
notice is emitted to stderr. The downstream-orchestrator cascade gate
still applies — `--reopen` does NOT auto-cascade.

The slash-command's own bash precheck mirrors this structure: gate-skip
when `--reopen` is set, gate-check otherwise.

```bash
if [ -z "$REOPEN" ]; then
    # Gate enforces target-must-not-exist
    [ ! -f "docs/plans/$SLUG/${SLUG}_plan.json" ] || { echo "REFUSE: target exists; use --reopen to overwrite"; exit 1; }
else
    # --reopen set: skip the target-exists check; CLI re-checks the cascade gate
    :
fi
```

## Parse the operator's tail for --amend

(This header is the apply-idempotency marker shared with the staged
apply script and the T6g `test_plan_md_amend_staged.py` discriminator.)

### `--amend` is the INVERSE gate (surgical patch, not overwrite)

`--amend` surgically patches an EXISTING `<slug>_plan.json` via a keyed
patch op-list instead of regenerating it wholesale. Its gate is the
exact INVERSE of the create gate above:

- `--amend` **REQUIRES** `<slug>_plan.json` to already exist — a missing
  target refuses with exit 1 (usage), the precise inverse of the
  create gate's target-EXISTS refusal.
- Unlike `--reopen`, `--amend` deliberately does NOT trigger the
  downstream-`<slug>_orchestrator.json` cascade refusal: you amend a
  plan precisely WHEN it has already been promoted to an orchestrator
  (operator-decision bypass). The reconcile-sync policy for the
  active-orchestrator case is the Phase-2 work (plan_surgical_amend
  §SC5).
- `--amend` and `--reopen` are **mutually exclusive** (opposite intents:
  surgical patch vs. wholesale overwrite); passing both refuses with
  exit 1.

On a successful amend the CLI (a) emits an `Amended: <field/key>` notice
to stderr — parallel to the `--reopen` `Reopened: overwrote <abs-path>`
notice — naming the touched keys in op-list order, and (b) appends ONE
JSONL row to the per-slug append-only audit log
`docs/plans/<slug>/<slug>_amend_log.jsonl` (schema:
`{timestamp, directive, ops, result}`). The audit log is the
accountability surface that makes a defeat-by-many-small-amends drift
traceable; it is APPEND-ONLY (a prior log is never truncated).

The substrate is `bin/_planner/main.py` (`_build_inputs` inverse gate +
the `patch_apply.apply_patch` dispatch + the atomic write/render/rollback
transaction). See plan_surgical_amend §SC6.

## Parse the operator's tail

When `$ARGUMENTS` contains more than just the slug, the main-agent
interpretation layer maps the tail prose to closed CLI flags. The
substrate (`bin/_planner/main.py`) only accepts canonical flags; this
slash command translates operator-natural prose into those flags.

**Step 1 — extract slug.** First whitespace-separated token = slug. Rest
= tail (preserving internal whitespace; trim outer).

**Step 2 — detect explicit flags.** If the tail begins with `--reopen`
(as a literal token), strip it and set `reopen=true`. If the tail begins
with `--amend` (as a literal token), strip it and set `amend=true`. Any
explicit `--directive "..."` substring is honored verbatim. `--reopen`
and `--amend` are mutually exclusive — if both are present, do NOT
guess; surface the conflict and let the CLI refuse (exit 1).

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

**Step 3.5 — detect amend-synonyms in prose.** If the tail contains any
of the canonical amend-synonym vocabulary (and NO redo-synonym fired),
set `amend=true`:

- `amend`
- `surgically`
- `tweak`
- `patch this`
- `fold in`
- `adjust the plan`
- `small fix to the plan`

Match case-insensitively. Same dual-role rule as redo-synonyms: detect
amend-synonym → emit `--amend=true` AND pass the full prose as
`--directive` so Call 1 reasons against the operator's full intent when
emitting the surgical patch op-list. A redo-synonym takes precedence
over an amend-synonym if both somehow appear (wholesale-overwrite intent
dominates); never emit both flags.

**Step 4 — pass remaining prose as directive.** After stripping any
literal flag tokens (e.g., a leading `--reopen` / `--amend` literal), the
remaining prose (trimmed) is the directive content. If non-empty, pass
it to the CLI as `--directive "<remaining>"`. The substrate enforces the
8KB UTF-8 cap; over-cap directives refuse with exit 1 (usage) before any
SDK call.

**Step 5 — surface the transparency notice.** When `--reopen` or
`--amend` was auto-extracted from prose (i.e., NOT from a literal token),
print a one-line operator-facing notice BEFORE running the CLI:

```
Interpreted '<original-tail-prose>' as --reopen + --directive '<remaining-prose>'
```

or, for an amend:

```
Interpreted '<original-tail-prose>' as --amend + --directive '<remaining-prose>'
```

Mirrors the `code.md:71-74` "Auto-picked T<n>" notice format. The notice
exists so the operator can see in-conversation what the prose-extraction
layer decided.

## Invocation examples

Run from the repo root under WSL2 / Ubuntu.

**Bare slug** (first run; no directive; no reopen):

```bash
bin/plan <slug>
# or with optional knobs:
bin/plan <slug> --tier 'Tier 2' --repo-state 'main @ commit abc123'
```

**Explicit flags** (operator types the canonical CLI form):

```bash
bin/plan <slug> --reopen --directive "focus on the §C gate logic"
```

**Surgical amend** (patch an existing plan in place via a keyed op-list):

```bash
bin/plan <slug> --amend --directive "tighten SC2's wording and add a non-goal about retries"
```

→ The CLI loads the existing `<slug>_plan.json` as the prior plan, runs
Call 2 in patch-mode (emitting a `plan_patch_v1` op-list), applies the
patch with byte-preservation of untouched entries, re-validates the
result against `plan_v1`, atomically re-writes the JSON + MD twin
(rolling back on a render failure), prints `Amended: <field/key>` to
stderr, and appends one row to `<slug>_amend_log.jsonl`.

**Prose-driven** (operator types natural language; this slash command
interprets):

```
/plan <slug> redo this from scratch and emphasize the §C gate logic
```

→ The slash-command body interprets as: `--reopen` (from "redo … from
scratch") + `--directive "redo this from scratch and emphasize the §C
gate logic"`, with a transparency notice surfaced first.

```
/plan <slug> surgically fold in the SC2 wording fix
```

→ The slash-command body interprets as: `--amend` (from "surgically" /
"fold in") + `--directive "surgically fold in the SC2 wording fix"`,
with a transparency notice surfaced first.

**Chain-driven invocation** (where the chain driver supplies
`remaining_chain_budget_usd` + `chain_id`):

```bash
bin/plan <slug> --chain-id <chain-id> --remaining-budget-usd <usd>
```

## Two-call mechanism

Per plan §D.1: Call 1 (reasoning, no `response_format`) + Call 2 (emission,
`response_format` set to `schemas/plan_v1.schema.json` — or, in amend mode,
`schemas/plan_patch_v1.schema.json`). Two distinct SDK calls; structural
enforcement (single-turn dual emission is impossible by construction).

See `.claude/agents/planner.md` for the subagent contract; `bin/_planner/
two_call.py` for the structural code.

## Exit codes

Per implplan §A.impl.3a shared closed-enum:

- 0  = success (JSON written to `docs/plans/<slug>/<slug>_plan.json`)
- 1  = usage error (slug dir missing, recon.md missing, directive over
       8KB cap, `--amend` on a missing plan.json, `--amend` + `--reopen`
       together, an over-bound amend patch, etc.)
- 5  = budget below floor (`OVERNIGHT_PLANNER_MIN_BUDGET_USD`)
- 7  = atomic write failed (also: an amend whose MD-twin render failed
       and was rolled back to its pre-amend bytes)
- 8  = target_exists_no_reopen (plan.json present and `--reopen` not
       set, or `--reopen` set but downstream orchestrator.json would
       become stale; use `--reopen` and address the cascade message
       in stderr)
- 16 = SDK retry exhausted (`error_max_structured_output_retries`) —
       operator edits schema or re-does recon/research and re-runs
- 43 = amend_post_apply_invalid (a surgical `--amend` patch applied
       cleanly but the resulting plan no longer validates against
       `plan_v1`; the engine refuses to persist a schema-broken plan)

## Cross-references

- `bin/plan` — POSIX shell wrapper
- `bin/_planner/main.py` — Python CLI entry
- plan §1.C — slash-command quick-reference + file-existence gate +
  `--reopen` contract
- plan §D.1-§D.7 — two-call planner full rationale
- `.claude/agents/planner.md` — subagent contract
- `std_command_operator_extensions` slug (TA + TD) — `--reopen` and
  `--directive` substrate additions; this MD's TG1 prose-extraction
  layer
- `plan_surgical_amend` slug (§SC6) — the `--amend` surgical-patch flag,
  the keyed `plan_patch_v1` op-list, the `Amended:` stderr notice, and
  the per-slug `<slug>_amend_log.jsonl` audit log
