"""`bin/fleet close <slug>` — the atomic terminal transition.

Field lesson (qum, 2026-07-20, second hub-rot incident in a week): a
closeout done by hand got the machine half right (dir archived, meta
reconciled, successor minted) while every hand-authored hub layer
lagged. This verb owns the WHOLE transition so nothing can lag:

1. final `closed` event + `_fleet.json` status flip (skipped when the
   slug is already closed — a second `close` COMPLETES a prior
   `--no-archive` half-close instead of failing);
2. meta reconcile: roster → `closed[]`, carrying `piece`, `wave`, a
   dated `closed` field, and the operator note. `close` is the one
   verb besides `init`/`migrate` allowed to write the meta;
3. archive: `docs/plans/<slug>/` → `docs/plans/_closed/<slug>/`
   (`git mv` inside a repo, plain move otherwise). `--no-archive`
   covers the observed closed-but-delivered half-state — status flips,
   archive deferred, and the renderer holds one-row-per-slug either
   way;
4. optional successor mint (one shot): roster row (+wave), slug dir,
   `_fleet.json` at seed/ready with the bare next-stage token (so the
   PROMPTS zone offers its spawn line) and the stored spawn directive;
5. ONE `render --write`.

Every refusal (unknown slug, already archived, successor exists,
incomplete successor spec) fires BEFORE any mutation. Concurrency story
is `update`'s: per-slug files, single-author meta.
"""

from __future__ import annotations

import shutil
import subprocess

from bin import _env_paths
from bin._fleet import engine, paths


class CloseRefused(Exception):
    """Pre-mutation refusal; message is operator-facing."""


def _closed_root():
    return paths.plans_dir() / "_closed"


def _archive_dir(slug: str):
    return _closed_root() / slug


def _git_mv(src, dst) -> bool:
    """`git mv` when the project is a work tree; False → caller falls back."""
    root = _env_paths.project_root()
    probe = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
    )
    if probe.returncode != 0 or probe.stdout.strip() != b"true":
        return False
    moved = subprocess.run(
        ["git", "-C", str(root), "mv", str(src), str(dst)],
        capture_output=True,
    )
    return moved.returncode == 0


def close(
    slug: str,
    *,
    note: str | None = None,
    no_archive: bool = False,
    successor: str | None = None,
    successor_piece: str | None = None,
    successor_wave: int | None = None,
    successor_next: str | None = None,
    successor_directive: str | None = None,
) -> str:
    meta = engine.load_meta()
    roster = meta.setdefault("roster", {})
    closed_list = meta.setdefault("closed", [])
    state = engine.load_state(slug)
    live_dir = paths.slug_dir(slug)

    # ── refusals, all pre-mutation ────────────────────────────────────
    if _archive_dir(slug).exists():
        raise CloseRefused(
            f"{slug} is already archived at {_archive_dir(slug)} — nothing to do"
        )
    if state is None and slug not in roster and not live_dir.is_dir():
        raise CloseRefused(
            f"unknown slug {slug!r}: no state, no roster entry, no "
            f"{live_dir} — check the spelling"
        )
    if successor is not None:
        if not (successor_piece and successor_wave is not None and successor_next):
            raise CloseRefused(
                "--successor requires --piece, --wave, and --next together "
                "(the mint is one shot — no partial successors)"
            )
        if (paths.slug_dir(successor).exists()
                or _archive_dir(successor).exists()
                or successor in roster):
            raise CloseRefused(
                f"successor {successor!r} already exists (dir, archive, or "
                f"roster) — pick a fresh slug"
            )

    actions: list[str] = []
    ts = engine._now_iso()
    date = ts[:10]

    # ── 1. final event + state flip (idempotent on re-close) ──────────
    already_closed = bool(state) and state.get("status") == "closed"
    if live_dir.is_dir() and not already_closed:
        engine.update(
            slug,
            status="closed",
            next_action="—",
            actor="fleet-close",
            note=note or f"closed {date}",
        )
        actions.append("state closed")

    # ── 2. meta reconcile (roster → closed[], single save below) ──────
    roster_entry = roster.pop(slug, {})
    if all(c.get("slug") != slug for c in closed_list):
        entry: dict = {
            "slug": slug,
            "piece": roster_entry.get("piece",
                                      (state or {}).get("piece", "")),
            "closed": date,
            "note": note or f"→ _closed/ ({date})",
        }
        if roster_entry.get("wave") is not None:
            entry["wave"] = roster_entry["wave"]
        closed_list.append(entry)
        actions.append("meta roster → closed[]")

    # ── 4-prep. successor mint lands in the same meta write ───────────
    if successor is not None:
        roster[successor] = {"piece": successor_piece, "wave": successor_wave}
    engine.save_meta(meta)

    # ── 3. archive ────────────────────────────────────────────────────
    if live_dir.is_dir() and not no_archive:
        _closed_root().mkdir(parents=True, exist_ok=True)
        if not _git_mv(live_dir, _archive_dir(slug)):
            shutil.move(str(live_dir), str(_archive_dir(slug)))
        actions.append(f"archived → _closed/{slug}/")
    elif no_archive:
        actions.append("archive deferred (--no-archive); re-run close to complete")

    # ── 4. successor mint ─────────────────────────────────────────────
    if successor is not None:
        paths.slug_dir(successor).mkdir(parents=True, exist_ok=True)
        engine.update(
            successor,
            stage="seed",
            status="ready",
            next_action=successor_next,
            piece=successor_piece,
            wave=successor_wave,
            actor="fleet-close",
            note=f"minted as successor of {slug}",
            spawn_directive=successor_directive,
        )
        actions.append(f"successor {successor} minted at seed/ready")

    # ── 5. one render ─────────────────────────────────────────────────
    engine.render_hub_write()
    actions.append("hub rendered")

    return f"closed {slug}: " + "; ".join(actions)
