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
bin/fleet state <slug>
bin/fleet render --write
```

**status** ∈ `ready` (next stage runnable) · `wip` (a stage in flight) ·
`done` (pipeline gates cleared) · `blocked` · `parked` · `closed`
(archived).

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

## Files

- `bin/fleet` — POSIX wrapper → `python -m bin._fleet.main`.
- `bin/_fleet/engine.py` — per-slug IO + the pure render projection.
- `bin/_fleet/hub.py` — `init` + `migrate` (anchor-verified swap).
- `bin/_fleet/seed.py` — one-time state seeding from operator JSON.
- `bin/_fleet/auto.py` — the engine-side stage hooks + the canonical
  stage → next-command map.
- `bin/_fleet/exit_codes.py` — closed enum (45 =
  `fleet_not_initialized`, 46 = `hub_anchor_missing`).
