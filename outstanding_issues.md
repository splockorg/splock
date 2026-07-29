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

## OI-4 — `wrap` and `close` have no agent-facing surface; the lifecycle does not drive its own brackets (2026-07-29, found while writing `docs/VISION.md`)

The two moments that bracket a slug are the two the lifecycle does not drive.
Both ship as working CLIs with no command, subagent, or skill surface:

- **`bin/wrap`** — wraps external content in a canonical delimiter pair over the
  closed `WrapKind` enum (`recon-findings`, `qa-findings`, `qna-findings`,
  `research-findings`, `call1-reasoning`, `lessons-findings`,
  `operator-directive`, `eli5-subject`). This is the trust boundary for content
  entering the plan record — the mechanism that keeps read content data rather
  than instruction.
- **`bin/fleet close`** — the atomic terminal transition: final event + archive
  + meta reconcile + successor mint + one render.

**Documentation defect found alongside it (VISION §4.14 class):** `README.md`'s
stage table lists `/wrap` as a lifecycle stage. There is no `commands/wrap.md`,
and `bin/wrap` is not a closeout stage — it is an input-sanitization boundary.
The vision's §5 lifecycle diagram carried the same error and has been corrected
to end at `close`, with `wrap` and `close` described as mechanisms alongside the
sequence rather than stages in it. **README.md still needs the same correction.**

**Why this is `outstanding` and not a marker or a slug** (VISION §9): it fails
the marker test — there is no named prerequisite; nothing blocks this work. It
fails the "write a task for it today" test too, because the shape is genuinely
undecided:

- Should closeout be a stage command (`/close`), a subagent, or an automatic
  consequence of the terminal transition already firing?
- Should `wrap` be agent-invoked **at all**? It is a trust boundary, and the
  agent is the party it bounds — an agent that can choose when to wrap its own
  input can choose not to. The safer reading is that `wrap` should be invoked
  *by the ingesting engine*, never by the agent supplying the content, in which
  case the correct fix is not an agent surface but a call-site audit.

That second question is the uncertain one, and it is why this sits here rather
than in a plan. Tracked in VISION §16.13.
