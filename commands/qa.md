---
description: Qa a splock slug — adversarial review of a slug artifact (recon by default; --subject selects recon/qna/research/plan) against a deterministically-constructed rubric
argument-hint: <slug> [free-text]
---

# /qa — operator-direct qa CLI entry

Triggered by the operator with: `/qa $ARGUMENTS`

Where `$ARGUMENTS` is **one or more** tokens, space-separated:

- **One token** (`<slug>` only) — bare invocation; no directive, no reopen.
- **Two or more tokens** (`<slug> <free-text-tail>`) — the first token is
  the slug; the remainder is operator-authored prose that this command
  interprets as either an explicit flag (`--reopen`, `--directive "..."`),
  a redo-synonym (mapped to `--reopen`), or directive content (passed
  through to the CLI as `--directive`). See the `## Parse the operator's
  tail` section below.

This command runs the single-call qa pass against the slug artifact
selected by `--subject` (default `recon` → `docs/plans/<slug>/<slug>_recon.md`).
It produces `<slug>_qa.md` for the recon subject, or `<slug>_qa_<subject>.md`
for a non-recon subject, via `bin/qa` (POSIX shell wrapper around
`python -m bin._qa.main qa`).

## Re-run modes (append / new-file / overwrite)

Per v2.7 §1.C, the only predecessor gate:

- REFUSE if the selected subject artifact
  `docs/plans/<slug>/<slug>_<subject>.md` does NOT exist (default subject
  `recon` → `<slug>_recon.md`; run the producing agent first).

An existing `<slug>_qa.md` is NO LONGER a refusal. `bin/_qa/main.py` now
resolves a re-run mode at write time (it no longer raises exit 8):

- **append** (DEFAULT, no flag): the new adversarial pass is appended to
  `<slug>_qa.md` under a provenance separator. NL: "update / revise / add to
  the qa".
- **new-file** (`--new-file`): write the pass to the next free
  `<slug>_qa_<N>.md`. NL: "new file / second pass / separate qa".
- **overwrite** (`--reopen`): use `--reopen` to overwrite `<slug>_qa.md` in
  place. NL redo-synonyms ("redo", "from scratch").

`--reopen` and `--new-file` are mutually exclusive (the CLI returns exit 1).
This slash command maps the operator's NL tail to the right flag (see
`## Parse the operator's tail`); the flags remain available directly.

(qa's review SUBJECT is selected by `--subject {recon,qna,research,plan}`
(default `recon`). The CLI-lagging-subagent split noted in earlier revisions
is now CLOSED — the CLI takes `--subject`, matching the in-Claude qa subagent
surface in `.claude/agents/qa.md`. Non-recon subjects write their own
`<slug>_qa_<subject>.md` (e.g. `<slug>_qa_plan.md`); the planner does NOT
auto-ingest those — qa-on-plan output is operator-folded via
`/plan <slug> --reopen`.)

## Parse the operator's tail

When `$ARGUMENTS` contains more than just the slug, the main-agent
interpretation layer maps the tail prose to closed CLI flags. The
substrate (`bin/_qa/main.py`) only accepts canonical flags; this slash
command translates operator-natural prose into those flags.

**Step 1 — extract slug.** First whitespace-separated token = slug. Rest
= tail (preserving internal whitespace; trim outer).

**Step 2 — detect explicit flags.** If the tail begins with `--reopen`
(as a literal token), strip it and set `reopen=true`. Any explicit
`--directive "..."` substring is honored verbatim.

**Step 2.5 — detect the review subject in prose.** ORTHOGONAL to the
re-run mode (Step 3) — subject and re-run mode are independent axes. Map
the operator's prose to ONE `--subject` value (a literal `--subject <s>`
token is honored verbatim):

- "review the plan" / "qa the plan" / "review `<slug>_plan.md`" → `--subject plan`.
- "review the research" → `--subject research`.
- "qa the qna" / "review the qna" → `--subject qna`.
- default, no subject named → `--subject recon` (the explicit default).

**Step 3 — detect the re-run mode in prose.** Map ONE mode (default
`append` = no flag):

- **overwrite** → set `reopen=true` (pass `--reopen`) ← redo-synonyms:
  `redo`, `restart`, `resume`, `regenerate`, `from scratch`, `retry`,
  `redo from scratch`, `open this again`, `redo it`, `overwrite`, `replace`.
- **new-file** → set `new_file=true` (pass `--new-file`) ← `new file`,
  `second file`, `separate file`, `second pass`, `another qa`, `additional`,
  `branch`.
- **append** → no mode flag (the CLI default) ← `update`, `revise`, `add to`,
  `append`, `extend`, OR no mode signal at all.

`--reopen` and `--new-file` are mutually exclusive; never emit both.

Match case-insensitively. The dual-role rule: detect redo-synonyms →
emit `--reopen=true` AND pass full prose as `--directive`. The synonym
trigger does NOT consume the prose; the operator's full original tail
flows through as directive content. (Step 4 then strips the literal
flag-form tokens from the directive content, but the natural-language
redo phrasing remains so the subagent sees the operator's full intent.)

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

When a `--subject` was resolved (from prose per Step 2.5, or a literal
`--subject <s>` token), the notice ALSO names the resolved subject:

```
Interpreted '<original-tail-prose>' as --subject <subject> (+ --reopen / --directive as applicable)
```

Mirrors the `code.md:71-74` "Auto-picked T<n>" notice format. The notice
exists so the operator can see in-conversation what the prose-extraction
layer decided.

## Invocation examples

Run from the repo root under WSL2 / Ubuntu.

**Bare slug** (first run; no directive; no reopen):

```bash
bin/qa <slug>
```

**Explicit flags** (operator types the canonical CLI form):

```bash
bin/qa <slug> --reopen --directive "focus on the gate logic in §C"
```

**Prose-driven** (operator types natural language; this slash command
interprets):

```
/qa <slug> redo this from scratch and focus on the §C gate logic
```

→ The slash-command body interprets as: `--reopen` (from "redo … from
scratch") + `--directive "redo this from scratch and focus on the §C
gate logic"`, with a transparency notice surfaced first.

**Chain-driven invocation** (where the chain driver supplies `chain_id`
for forensic logging):

```bash
bin/qa <slug> --chain-id <chain-id>
```

## Single-call mechanism

Per plan §D.8.3: ONE `messages.create(...)` invocation per qa pass. No
`output_config` — qa output is structured MD, not JSON. The block
A/B/C/D taxonomy is enforced prompt-side via the deterministic rubric
in `bin/_qa/rubric.py::RUBRIC_MD`; structural decoding does NOT apply
because the output target is markdown.

Contrast with `/plan` and `/implplan`, which use the two-call
constrained-decoding mechanism (`bin/_planner/two_call.py`) because
their output targets are JSON.

See `.claude/agents/qa.md` for the subagent contract (the parallel
Claude-Code-internal invocation surface, which uses Read/Grep/Glob for
claim-against-tree verification — distinct from this CLI substrate,
which is SDK-direct text-only).

## Determinism of the rubric

Per plan §D.8.3 + research_findings_v1.md §D: the qa rubric MUST be
deterministically constructed (NEVER agent-authored). This CLI's
rubric is the `RUBRIC_MD` Python constant in `bin/_qa/rubric.py`.
Updates to that constant are operator-side commits guarded by
`tests/splock/test_qa/test_rubric_byte_stability.py`, which
pins every assembled per-subject rubric byte-for-byte against a
committed snapshot fixture so the rubric cannot drift mid-conversation
or across releases.

## Exit codes

Per the closed enum in `bin/_qa/exit_codes.py`:

- 0  = success (MD written to `docs/plans/<slug>/<slug>_qa.md`)
- 1  = usage error (slug dir missing, recon.md missing, directive over
       8KB cap, etc.)
- 7  = atomic write failed
- 8  = target_exists_no_reopen — RETAINED for cross-CLI parity but NO
       LONGER raised by qa (re-runs append by default; nothing to refuse)
- 17 = SDK call failed (qa's analogue of `/plan`'s exit 16; distinct
       numeric so callers can disambiguate)

## Cross-references

- `bin/qa` — POSIX shell wrapper
- `bin/_qa/main.py` — Python CLI entry
- `bin/_qa/invoke.py` — SDK invocation
- `bin/_qa/rubric.py` — deterministic rubric constant
- `.claude/agents/qa.md` — Claude-Code subagent contract (parallel surface)
- plan §D.8.3 — qa subagent prompt-content spec
- v2.7 §1.C — file-existence gate semantics + `--reopen` contract
- v2.7 §1.D — common subagent body machinery
- `std_command_operator_extensions` slug (TB + TE) — `--reopen` and
  `--directive` substrate additions; this MD's TG1 prose-extraction
  layer
