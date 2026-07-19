"""`bin/fleet` — concurrency-safe multi-slug lifecycle tracker.

WHY: many agents editing one shared status/launcher `.md` via
read-modify-write means last-writer-wins clobbering, stale-snapshot
edits, and repeated full-file re-reads (token waste). fleet removes the
single shared write target.

SOURCE OF TRUTH = per-slug files (collision-free by construction — no
two agents ever write the same path):

    docs/plans/<slug>/_fleet.json        current state (overwrite; per-slug)
    docs/plans/<slug>/_fleet_log.jsonl   append-only event history (per-slug)

CROSS-SLUG STRUCTURE (rarely changes, single author):

    docs/plans/_fleet/_fleet_meta.json   waves + roster + closed + legend
                                         + the hub .md path

DERIVED VIEW (a build artifact — never hand-edit the marked zones):

    the hub .md — `bin/fleet render --write` regenerates ONLY the
    `FLEET:*` marker zones (▶ Now / status board / recent events).

Modules:

- `engine`     — the faithful port of the reference implementation
                 (qum `scripts/fleet/fleet.py`, provenance commits
                 a252ef3 + 00f3297): per-slug state/event IO + the pure
                 render projection. All four safety properties are kept
                 verbatim in behavior: atomic state swap (tmp +
                 `os.replace`), append atomicity (single `O_APPEND`
                 write, line < PIPE_BUF, per-slug path), torn-line-
                 tolerant fold, and verify-anchors-before-single-
                 atomic-swap (in `hub`).
- `paths`      — splock-side generalization of the reference's three
                 path constants (`PLANS_DIR`, `LAUNCHER_MD`,
                 `META_PATH`): everything resolves through
                 `bin._env_paths` per call, so the CLI operates on the
                 ADOPTER project (opt-in: fleet is active for a project
                 iff `docs/plans/_fleet/_fleet_meta.json` exists).
- `hub`        — `init` (scaffold or register the hub) + `migrate`
                 (wire the `FLEET:*` zones into an existing hand-edited
                 hub: verifies EVERY anchor before one atomic swap;
                 idempotent).
- `seed`       — one-time authoring of per-slug `_fleet.json` from an
                 operator-supplied JSON (idempotent; `--force`;
                 `--events`). The qum roster CONTENT stayed in qum —
                 only the mechanics ported.
- `auto`       — the splock-only addition: stage engines and stage
                 commands call these hooks on stage start (`wip`) and
                 completion (`ready --next <next>` / `done` / `closed`)
                 so tracking is a side effect of running a stage, never
                 hand bookkeeping. Every hook is a silent no-op when the
                 project has not opted in, and never raises into the
                 calling engine.
- `main`       — argparse CLI (`update` / `render` / `state` / `init` /
                 `seed` / `migrate` / `stage`), dispatched from the
                 POSIX wrapper `bin/fleet`.
"""
