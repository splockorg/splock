# Outstanding issues

> Scaffold placeholder. Empty by design — deferred-work entries that the
> framework routes here accumulate over time. This file is the canonical
> outstanding-issues log for the splock repo's own plan-state convention.

## OI-1 — `_PLANS_DIR` hardcoded to plugin tree; adopter repos wrote plans into the install dir (2026-07-07, found on QUM adoption)

Eight `bin/_*` entry points (`_planner/main.py`, `_retry_loop/main.py`,
`_render_plan/{main,verify,migrate}.py`, `_chain_overnight/main.py`,
`_chain_pause/main.py`, `_chain_resume/main.py`) each derived
`_PLANS_DIR = Path(__file__).parents[2] / "docs" / "plans"` — the plugin's
own tree. Running any CLI against an adopter repo (first hit: `bin/plan` on
QUM) would read/write `docs/plans/` inside the plugin install/cache dir
instead of the adopter project, violating the PLUGIN_ENV_CONTRACT rule that
`parents[2]` is read-only-assets-only. `CLAUDE_PROJECT_DIR` was documented
in ADOPTION.md but never consulted for plan substrate.

**Fix (landed in working tree, same day):** `bin/_env_paths.project_root()`
+ `plans_dir()` (`$CLAUDE_PROJECT_DIR` → `parents[2]` fallback, preserving
sideloaded/in-tree behavior); all eight call sites rewired to
`_PLANS_DIR = plans_dir()`. pytest 130 passed (3 pre-existing
git-history-hygiene failures unrelated, see OI-2), trace_grep clean.

**Follow-up:** audit remaining `parents[2]` users (`_eval_baseline`,
`_eval_trend`, `_render_status_tree`, `_render_log`, `_sealed_rm`,
`_planner/reconcile.py`) for the same class; add a regression test that sets
`CLAUDE_PROJECT_DIR` to a tmp dir and asserts substrate lands there.

## OI-2 — `test_smoke_battery` git-history hygiene tests fail on any local dev commit (2026-07-07)

`test_exactly_one_commit`, `test_commit_author_is_public_org_identity`,
`test_no_personal_identity_anywhere_in_history` assert the frozen
single-release-commit state; the local dev commit c489749 ("docs: document
dev venv", personal author identity) breaks all three by construction.
Working tree stays scrub-clean (trace_grep 0 traces). Needs a decision:
squash/re-author before any push to splockorg/splock, and/or scope these
tests to CI-on-origin only so local dev iteration keeps a green suite.

## OI-3 — QUM adoption Phase-1 findings (F1–F8) (2026-07-08, first foreign-adopter run)

Driving the first real QUM slug (`component_catalog_sandbox`) through
`/plan → /implplan → /code → /test` surfaced eight fork bugs. Full repro,
evidence, and root-cause detail live in the adopter repo at
`qum/docs/onboarding/splock-fork-findings-phase1.md`. Status as of this
working tree (fork suite: **130 passed / 3 failed** — the 3 are OI-2
history-hygiene; trace_grep clean; `plugin validate --strict` ✔):

| ID | Bug | Status |
|----|-----|--------|
| F6 | Hook `REPO_ROOT` off-by-one (`$SCRIPT_DIR/../..`) → `No module named 'bin'` on every `python -m bin.*` hook in an adopter repo | **FIXED** — layout-robust resolver in all 19 `hooks/*.sh` (committed on branch `fix/hook-repo-root-plugin-layout`) |
| F2 | `bin/_orchestrator_query` used `parents[2]`, ignoring `$CLAUDE_PROJECT_DIR` (the un-audited tail of OI-1) → `/code` picker dead for adopters | **FIXED** — `_repo_root()` now delegates to `_env_paths.project_root()` |
| F3 | `run_verify_subprocess` used plugin root + `sys.executable` (plugin venv) + bare-name positional args → `/test` can't grade adopter tests | **FIXED** — adopter root via `project_root()`, adopter interpreter (`$SPLOCK_TEST_PYTHON` / `<root>/.venv`), `-k` selector |
| F1 | Three `*_md_canonical.md.template` files never shipped → every plan/orch/state render exits 6 (`template_error`) | **FIXED** — ported the render templates into `.claude/templates/` (renderer is byte-identical to the embedded repo's); plan/orch/status-tree renders now exit 0 |
| F4 | Planner needs `anthropic`, retry loop needs `claude-agent-sdk`; neither declared → adopter fresh-venv hits ModuleNotFoundError / `sdk_smoke_failed` | **FIXED** — added `requirements-sdk.txt` (version floors match verified contracts) + ADOPTION.md note |
| F7 | Wrappers `exec python`; ADOPTION.md documents a `python3` fallback the code never implemented → `python: not found` on python3-only hosts (2 venv-smoke tests red) | **FIXED** — 27 `bin/` wrappers now `exec "$(command -v python \|\| command -v python3)"`; both venv-smoke tests green. **Follow-up:** the `hooks/*.sh` `python -m` calls (pipe/conditional forms) still use bare `python` — same fallback wanted, deferred (non-uniform forms interleave with F6 comments) |
| F5 | Plan JSON `phase: "Phase 2"` vs orchestrator `phase: "Phase 3"` — LLM-emitted, no carry-forward | **FILED** — cosmetic; fix = stamp `orchestrator["phase"] = plan["phase"]` at the `_read_prior_plan_json` seam (deferred: core emission path, not worth the risk for a label) |
| F8 | `bin/render_plan --kind state` rejects a schema-less `_state.json` (exit 4) while `render_invoker.py:196` + the status-tree path tolerantly default `schema_version=1` | **FILED** — minor path inconsistency; fix = route the `--kind state` json_loader through the same defaulting, or have the state writer emit `schema_version` |

All fixes are in the working tree (not yet committed except F6 on its
branch). Per the Phase-1 vs Phase-2 split, landing these as PRs on
`splockorg/splock` is Phase-2 backport work gated on the OI-2 identity
decision. F2/F3 are the same adopter-root class OI-1 fixed; the OI-1
follow-up ("audit remaining `parents[2]` users") should fold in F2/F3 and
add a plugin-mode regression test (run each CLI from a foreign cwd with
only `$CLAUDE_PROJECT_DIR` set).

## OI-4 — `wrap` invocation authority is undecided: should the wrapped party ever call the wrapper? (2026-07-29, found while writing `docs/VISION.md`)

`bin/wrap` wraps external content in a canonical delimiter pair over the closed
`WrapKind` enum (`recon-findings`, `qa-findings`, `qna-findings`,
`research-findings`, `call1-reasoning`, `lessons-findings`, `operator-directive`,
`eli5-subject`). It is the **trust boundary for content entering the plan
record** — the mechanism that keeps content an agent merely read as data rather
than instruction. It ships as a CLI with no command, subagent, or skill surface.

The open question is not "does it need an agent surface" — it is whether it may
have one:

- `wrap` bounds the agent, and **the agent is the party it bounds.** An agent
  that can choose when to wrap its own input can choose not to. That is the
  structure VISION §4.1 exists to refuse.
- The safer reading is that `wrap` should be invoked **by the ingesting engine**,
  never by the agent supplying the content — in which case the correct fix is not
  an agent surface at all but a **call-site audit**: every path where external
  content reaches the planner should wrap it deterministically, and any path
  where an agent hands over already-wrapped content is a hole.

**Why this is `outstanding` and not a marker or a slug** (VISION §9): nothing
blocks it, so there is no prerequisite to name — it fails the marker test. And
no task can be written today, because the two candidate resolutions (agent
surface vs. call-site audit) point in opposite directions and the audit has not
been done. Tracked in VISION §16.13.

**Related but separate:** `OI-5` covers slug closeout. The two were filed
together on 2026-07-29 and split the same day — they share only "CLI with no
agent surface", they sit at opposite ends of the lifecycle, and they want
opposite resolutions.

## OI-5 — slug closeout has no agent-facing surface; the lifecycle does not drive its own terminal transition (2026-07-29, found while writing `docs/VISION.md`)

`bin/fleet close` performs the atomic terminal transition of a slug: final event
+ archive + meta reconcile + successor mint + one render, or none of it. It works
and it is concurrency-safe. It has no command, subagent, or skill surface, so
**the last act of the lifecycle is the one act the lifecycle does not perform** —
an operator runs it by hand, or it does not happen.

That asymmetry is the defect. Every other stage records its own state
transitions automatically (VISION §10, "running the lifecycle *is* the
bookkeeping"); closeout is the exception, which makes a finished slug's terminal
state depend on a human remembering.

Open shape:

- Should closeout be a stage command (`/close`), a subagent, or an **automatic
  consequence** of the last task reaching a terminal state?
- The automatic option is the most consistent with §10 and the most dangerous —
  a closeout that fires on its own must be certain the slug is actually finished,
  and "actually finished" is the judgment the completion gate makes per task, not
  per slug.

**Why this is `outstanding`** (VISION §9): no prerequisite blocks it, and the
three candidate shapes have materially different blast radii, so no task is
writable today. Tracked in VISION §16.14.

## OI-6 — splock's own outstanding ledger sits outside its own sealed inventory (2026-07-29) — ✅ FIXED 2026-08-05

`hooks/sealed_paths.txt` seals the deferred-work ledger at
**`docs/outstanding_issues.md`**, so in an adopter repo `bin/route_issue` is the
sole writer and a raw agent edit is denied at the `PreToolUse` boundary. splock
keeps its own ledger at the repo **root** — `outstanding_issues.md` — which
matches no sealed glob.

**Consequence:** in this repo an agent can hand-edit the ledger that is supposed
to be CLI-only. That is not hypothetical — `OI-4`, `OI-5`, and this entry were
all written with a raw editor, which an adopter would have been refused for.
Same family as the empty marker registry: **splock governs its adopters more
strictly than it governs itself** (VISION §4.15).

**Candidate resolutions** (as filed):

1. **Move the ledger** to `docs/outstanding_issues.md`, matching the sealed glob
   and the adopter convention. Cheapest, and makes splock's layout match what it
   tells adopters to do. Costs: a path change in anything that references it.
2. **Add the root spelling** to the sealed inventory, accepting two legal
   locations. Cheaper still, but leaves splock's own layout diverging from the
   convention it ships.
3. **Seal by basename** rather than path. Broadest; risks over-sealing an
   adopter's unrelated file of the same name — though per the inventory's own
   comment, over-sealing is the safe direction.

**FIXED 2026-08-05 by option 1 — the ledger moved to
`docs/outstanding_issues.md`.** One location, one convention, no special case.
Every other part of the machinery already pointed at the doc-rooted path: the
sealed glob, `bin/_route_issue/outstanding.py`'s writer, and
`hooks/lazy-dump-cap.sh`'s staged-diff check. Only the file was in the wrong
place — so this repo's ledger was neither sealed *nor* cap-enforced, while
`bin/route_issue` would have created a second, empty ledger beside it on first
use. Raw edits are now denied at the `PreToolUse` boundary here exactly as in
an adopter, and appends go through `bin/route_issue --type outstanding`.
References repointed: `AGENTS.md`, `docs/IMPLEMENTATION_STATUS.md` (three rows),
`bin/scaffold_check.sh`.

**Correction to this entry's own claim** (VISION §4.14 class): the original text
said the fix "needs an operator" because `hooks/**` is sealed. That was true of
options 2 and 3 and false of option 1, which was the recommendation — moving the
file touches no sealed path. The un-fixability was overstated, and it is the
reason this sat for a week.

**Related:** the same "shipped for adopters, unused here" pattern covers the
empty `docs/plans/scheduled_markers/list.md` and `prefix_registry.md` — tracked
in `docs/IMPLEMENTATION_STATUS.md` under *Partially built*.

---

## Documentation defect found alongside OI-4/OI-5 (VISION §4.14 class) — README stage table — ✅ FIXED 2026-07-29

`README.md`'s stage table listed `/wrap` as a lifecycle stage. There is no
`commands/wrap.md`, and `bin/wrap` is not a closeout stage — it is an
input-sanitization boundary. `VISION.md` §5 carried the same error.

**Both corrected 2026-07-29.** The README stage table now ends at `/eli5` and
carries a separate table for the two non-stage mechanisms (`bin/wrap`,
`bin/fleet close`), stated as unrelated to each other. `VISION.md` §5 matches.
- [2026-08-05T23:45:27Z] [splock] [] [bin/route_issue:outstanding] mysql-mcp-guard trusts .mcp.json, which no sealed glob covers — an agent can repoint a lane's credentials or its declared credential command mid-session
  - context: bin/_mysql_mcp_guard/probe.py + hooks/sealed_paths.txt (fix is one line in the seal list; hooks/** is agent-sealed, so it needs an operator)
  - line_id: oi_2026-08-05T23:45:27Z_2328
  - status: open
- [2026-08-05T23:45:27Z] [splock] [] [bin/route_issue:outstanding] mysql-mcp-guard probe needs a mysql/mariadb client binary; a Python-only host refuses every lane with no_client even when the credential is read-only
  - context: bin/_mysql_mcp_guard/probe.py shutil.which branch — fix is an optional adopter-driver (pymysql) transport, deferred to keep a second grants-parsing path out of a security gate
  - line_id: oi_2026-08-05T23:45:27Z_566b
  - status: open
- [2026-08-05T23:46:43Z] [splock] [] [bin/route_issue:outstanding] splock does not enable its own plugin in its own checkout, so none of its hooks constrain agents working here — the seal inventory is inventory-only in this repo (§4.15 root of the OI-6 family)
  - context: .claude/settings.local.json has no splock@splock entry; adopters carry it. Found while closing OI-6
  - line_id: oi_2026-08-05T23:46:43Z_98ed
  - status: open
