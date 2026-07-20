# fleet — concurrency-safe multi-slug lifecycle tracking

**Problem it solves.** A project running many slugs through the splock
lifecycle wants one human view of where everything stands — a status
hub. The naive implementation is a shared status `.md` that every agent
edits via read-modify-write, which means last-writer-wins clobbering,
stale-snapshot edits, and repeated full-file re-reads (token waste).
`fleet` removes the single shared write target.

The design is a faithful port of a reference implementation proven in
production elsewhere (per-slug JSON files joined at render time);
splock adds the part the reference left manual: **stage engines record
lifecycle transitions automatically**, so agents never hand-edit the
hub and never even have to remember the tracker exists.

## Model

| Thing | File | Written by | Contention |
|---|---|---|---|
| **Current state** (per slug) | `docs/plans/<slug>/_fleet.json` | that slug's stage run (overwrite) | none — one path per slug |
| **History** (per slug) | `docs/plans/<slug>/_fleet_log.jsonl` | append-only | none — per-slug, append |
| **Cross-slug structure** | `docs/plans/_fleet/_fleet_meta.json` | operator, rare | n/a |
| **Human view** (derived) | the hub `.md` `FLEET:*` zones | `bin/fleet render` (generated) | never hand-edited |

Because every write target is **per-slug**, any number of agents update
concurrently with **zero contention** — no clobbering, no stale
re-reads. Separate files, joined at render time by a glob.

The per-slug state and log files are **sealed** (`hooks/sealed_paths.txt`):
agents cannot Read/Edit/Write them directly — `bin/fleet` is the only
writer, and `bin/fleet state <slug>` is the read path.

## Opt-in

fleet is **opt-in per project** and inert everywhere else: every entry
point (the CLI's `stage` verb and the engine-side hooks) is a silent
no-op unless `docs/plans/_fleet/_fleet_meta.json` exists. To opt in:

```bash
bin/fleet init            # scaffolds docs/plans/_fleet/fleet.md (zones included)
```

or, to keep an existing hand-edited launcher/status hub:

```bash
bin/fleet init --hub docs/plans/my_launcher.md
bin/fleet migrate \
    --now-start "## Runnable now"  --now-end "## Status board" \
    --board-start "## Status board" --board-end "## History" \
    --recent-start "## History"     --recent-end "## Changelog"
```

`migrate` anchors on **stable section headers** (not volatile table
bodies), verifies **every** anchor before a **single** atomic swap, and
refuses byte-untouched with the full missing list otherwise. Zones you
don't anchor are appended as one generated section. Re-running is a
no-op once markers exist.

To carry an existing status board's content over once:

```bash
bin/fleet seed --from seed.json --events    # idempotent; --force to overwrite
```

where `seed.json` is `{"as_of": ..., "states": {"<slug>": {"stage": ...,
"status": ..., "next": ..., "blockers": ...}}, "events": [...]}` — see
`bin/_fleet/seed.py`. Existing `_fleet.json` files are skipped so
seeding never clobbers live agent state; `piece`/`wave` join in from
the meta roster.

## Automatic stage tracking (the splock addition)

Once a project has opted in, running the lifecycle IS the bookkeeping:

| Stage | Hooked where | start | on success | on verdict halt |
|---|---|---|---|---|
| `/recon` `/research` `/qna` | command (`bin/fleet stage …`) | `wip` | `ready --next` /qa · /plan · /plan | — |
| `/plan` `/implplan` | `bin/_planner/main.py` | `wip` | `ready --next` /implplan · /code | — |
| `/qa` | `bin/_qa/main.py` | `wip` | `ready --next /plan` | — |
| `/code` | `bin/_update_orchestrator` (per task) + command | `wip` (task-granular events) | `ready --next /test` when the task loop drains | — |
| `/test` | `bin/_retry_loop/main.py` | `wip` | `ready --next /review` | `blocked` |
| `/review` | `bin/_retry_loop/main.py` | `wip` | `ready --next` /implplan or /code (junction-scoped) | `blocked` |
| close | `bin/update_orchestrator --close` | — | `closed` | — |

Engine hooks live in `bin/_fleet/auto.py` and hold three invariants:
no-op unless opted in AND the slug dir exists; **never raise into the
calling engine** (a broken hub degrades to a stderr warning); render
best-effort after every mutation. Infrastructure failures (SDK errors,
driver crashes) append an event without flipping status; only
verdict-carrying halts mark a slug `blocked`.

Manual updates remain for out-of-band changes:

```bash
bin/fleet update <slug> --status parked --note "waiting on upstream" --render
bin/fleet update <slug> --spawn-directive "ingest the qa recs in Call 1"
bin/fleet state <slug>
bin/fleet render --write
```

**status** ∈ `ready` (next stage runnable) · `wip` (a stage in flight) ·
`done` (pipeline gates cleared) · `blocked` · `parked` · `closed`
(archived).

## The prompt bay (generated next actions)

First-field-deployment finding (qum, 2026-07-18): the one hub section
fleet didn't generate — a hand-authored "Prompt Bay" of per-slug
copy-paste kickoff blocks — rotted within a day (blocks survived three
stage progressions unedited; a closed slug's block outlived its own
archival). The root cause: the bay was *derived state expressed as
hand-authored prose*, and fleet owned every input except one — the
per-slug directive text that makes a spawn self-contained. Anything
derived that isn't generated will rot, so both halves are now fleet's:

- **The missing input is persisted.** `bin/fleet update <slug>
  --spawn-directive "<operator context>"` stores that text in the
  slug's `_fleet.json` (same contention-free write path as status). It
  round-trips through `bin/fleet state` and `board --json`; `""`
  clears it by hand.
- **`spawn` consumes it.** With no `--prompt-suffix`, `bin/fleet spawn`
  appends the stored directive to the child prompt; an explicit
  `--prompt-suffix` (even `""`) overrides it for that spawn only.
  Directives are **one-shot**: they target the stage about to run, so
  any stage completion clears the slug's directive — the bay never
  advertises consumed context to the next stage. (A `blocked` halt
  keeps it, for the retry/resume.)
- **The `FLEET:PROMPTS` zone renders only non-derived inputs.** Each
  ready slug gets a runnable one-liner — `bin/fleet spawn <slug>
  --stage <next>` — with the stored directive shown as an annotation,
  never embedded in the command: model/effort/budget resolve from the
  stage profile and the directive from state *at spawn time*, so a
  pasted line cannot carry stale config. Blocked/parked slugs form a
  held group with their blockers; wip/done/closed slugs drop
  automatically — closeout can't leave husks.

Hubs wired before this zone existed keep working unchanged (`render
--write` skips the absent markers; the original three zones stay
mandatory). To upgrade, re-run `bin/fleet migrate`: it wires ONLY
missing zones — between `--prompts-start/--prompts-end` anchors, or
appended as a compact marker block. Fresh `init` scaffolds include the
zone.

Doctrine (ADOPTION.md): retire hand-authored "what to run next"
sections on fleet adoption. Narrative around the zones stays yours —
wave gates, operator rulings, outside-repo hand-offs; the runnable
next actions are generated.

## Safety properties

Kept verbatim in behavior from the reference implementation:

- **Atomic state writes** — `_fleet.json` is written to a
  pid-suffixed `.tmp` then `os.replace` (atomic swap). The pid suffix
  goes beyond the reference: two engines writing the same slug at once
  (operator `/qa` racing a chain `/test`) each swap a complete file.
- **Append atomicity** — log lines are `< PIPE_BUF` (4 KiB — notes are
  clamped at append time) single `O_APPEND` writes, atomic on a local
  FS; and per-slug, so no cross-agent interleave regardless.
- **Torn-line tolerance** — the fold skips any unparseable log line; a
  partial append never corrupts a render.
- **No partial migration** — `migrate` verifies every anchor before its
  single atomic swap.

`tests/test_fleet_concurrency.py` exercises all of this with real
separate processes: 8 writers × 40 same-slug appends lose zero events,
and a reader polling `_fleet.json` during update churn never observes a
torn state file.

## Headless C&C (`spawn` / `board` / `resume`)

One parent session — the operator's single screen — forks **fresh,
headless Claude Code sessions**, one per task, each with its own
model/effort/permission config, running in the background on the
operator's **subscription**. The parent absorbs only each child's final
JSON result (a few KB), never its context; blockers centralize onto one
board; any child is re-enterable by session id with its full context
intact. Fresh-context-per-task AND nothing-ever-lost, simultaneously.

```bash
bin/fleet spawn <slug> --stage recon                  # profile-driven child
bin/fleet spawn <slug> --stage code --model claude-fable-5 --effort xhigh \
    --permission-mode acceptEdits --allowed-tools Bash Edit Write
bin/fleet board                                       # states + live children +
                                                      #   resume handles + cost
bin/fleet resume <slug> --directive "the DB was down; retry the migration"
```

**Transport (billing-model constraint, not style):** children are
spawned as `claude -p "/splock:<stage> <slug>" --output-format json`
CLI subprocesses — never via the Claude Agent SDK, which is
API-key-only by policy. Subscription OAuth works headless;
`CLAUDE_CODE_OAUTH_TOKEN` is honored for detached contexts (cron/CI);
`ANTHROPIC_API_KEY` is never read or required by the spawner. The child
runs with cwd = the project root, so it reads the project's CLAUDE.md
and inherits the fleet protocol; its own stage engines record
`wip`/`ready`/`blocked` — the spawner adds no scaffolding of its own,
only the slug's stored spawn directive (see §The prompt bay), which an
explicit `--prompt-suffix` overrides.

**Bookkeeping** stays per-slug: every spawn/resume appends to
`docs/plans/<slug>/_fleet_runs.jsonl` (same append discipline as the
event log) — a `spawned`/`resumed` row from the parent the moment the
command returns, and a `completed`/`failed` row from the detached
runner carrying `{session_id, total_cost_usd, is_error, denials,
result_snippet}`. Full child JSON + runner log land at
`docs/plans/_fleet/runs/<run_id>.{json,log}` (unique names — no shared
write target). The board is a pure fold: lifecycle per slug, live
children (runner pid), died runners, blocked slugs with copy-paste
resume commands, and the cumulative pool draw — torn rows and dead
children degrade to rendered warnings, never a crash. On subscription
OAuth the CLI's `total_cost_usd` is a notional API-rate equivalent (a
pool-draw meter, not billing), so the text board labels it "est. pool
draw"; `board --json` keeps the CLI-native `cost_usd` keys.

**Per-stage profiles** live in `_fleet_meta.json` (absent keys fall
through to the claude CLI's own defaults; CLI flags > stage profile >
`_defaults`):

```json
"profiles": {
  "_defaults": {"permission_mode": "default"},
  "code":  {"model": "claude-fable-5", "effort": "xhigh",
             "permission_mode": "acceptEdits",
             "allowed_tools": ["Bash", "Edit", "Write"]},
  "recon": {"model": "claude-opus-4-8", "effort": "high"}
},
"max_concurrent": 4,
"command_template": "/splock:{stage} {slug}"
```

`max_concurrent` exists because all children draw ONE subscription pool
(5-hour/weekly limits); `--max-budget-usd` adds a per-child spend
ceiling. A qum-era meta without these keys works unchanged (zero
migration): defaults apply.

**Verified platform facts** (live spike, 2026-07-18, CLI 2.1.214 —
re-verify before relying on version-sensitive behavior):

- `--output-format json` returns `{result, session_id, total_cost_usd,
  is_error, permission_denials, modelUsage, num_turns, usage, …}`.
- Headless `claude -p --resume <session_id> "<directive>"` re-enters
  with full context; the session id is unchanged (`--fork-session` to
  mint a new one).
- Under headless `default` permission mode a denied tool call is
  recorded in `permission_denials` and the child completes gracefully
  (`is_error: false`) — the board treats a non-empty denial list as a
  needs-attention signal alongside fleet-status `blocked`.
- Per-child `--model`, `--effort low|medium|high|xhigh|max`,
  `--permission-mode`, `--allowedTools`, `--max-budget-usd` all exist
  as first-class flags.
- Subscription OAuth works with `ANTHROPIC_API_KEY` unset (verified by
  spawning with the var explicitly removed).
- The `ultracode` keyword does **not** activate in `-p` children (no
  system reminder observed) — do not rely on it in child prompts.

## Closeout + the fully generated hub (`close`, TREE, ATTENDED)

Field lesson (two hub-rot incidents in one week): **state is never
hand-authored** — narrative/charter prose stays human; every layer
derivable from per-slug state is a generated zone; terminal transitions
propagate everywhere atomically.

**`bin/fleet close <slug>`** owns the whole terminal transition in one
verb: final `closed` event + state flip → meta reconcile (roster →
dated, waved `closed[]` row; `close` is the one verb besides
`init`/`migrate` that writes the meta) → archive to
`docs/plans/_closed/<slug>/` (`git mv` in a repo, plain move otherwise;
`--no-archive` defers the move for the closed-but-delivered half-state,
and a second `close` completes it) → optional one-shot successor mint
(`--successor <slug> --piece … --wave N --next "/<stage>"
[--successor-directive …]` — roster row, slug dir, seed/ready state, so
the PROMPTS zone offers its spawn line immediately) → ONE
`render --write`. Every refusal (unknown slug, already archived,
successor exists, partial successor spec) fires before any mutation
(exit 50).

**Two more generated zones** (same marker mechanism; `bin/fleet
migrate` upgrades wired hubs by adding only the missing zones):

- `FLEET:TREE` — the execution tree, derived per wave from roster +
  live state; closed slugs render collapsed with their closed date.
- `FLEET:ATTENDED` — the attended queue: ready slugs whose next stage
  is in the meta `unspawnable_stages` deny-list render here as
  attended-session gestures (`/splock:<stage> <slug>`), never as spawn
  lines. Optional per-slug `roster.<slug>.attended {slot, model,
  effort, ultracode}` renders when present (the seam a future
  routing advisor fills). The SAME deny-list makes `bin/fleet spawn`
  refuse outright (exit 47) — attended-only is policy, not a
  profile-absence accident.

## Files

- `bin/fleet` — POSIX wrapper → `python -m bin._fleet.main`.
- `bin/_fleet/engine.py` — per-slug IO + the pure render projection.
- `bin/_fleet/hub.py` — `init` + `migrate` (anchor-verified swap;
  upgrades wired hubs with missing zones).
- `bin/_fleet/close.py` — the atomic terminal transition (`fleet close`).
- `bin/_fleet/seed.py` — one-time state seeding from operator JSON.
- `bin/_fleet/auto.py` — the engine-side stage hooks + the canonical
  stage → next-command map.
- `bin/_fleet/runs.py` — the per-slug C&C runs ledger.
- `bin/_fleet/spawn.py` + `spawn_runner.py` — headless child spawner
  (CLI-subprocess transport) + the detached result-capturing runner.
- `bin/_fleet/board.py` — the C&C fold (`fleet board [--json]`).
- `bin/_fleet/exit_codes.py` — closed enum (45 =
  `fleet_not_initialized`, 46 = `hub_anchor_missing`, 47 =
  `spawn_refused`, 48 = `no_session`).
