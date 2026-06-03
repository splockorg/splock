"""Closed-state lifecycle for an orchestrator slug (plan_surgical_amend T4 / SC4).

A plan's `_state.json` carries an **additive** top-level `lifecycle` field
(mini-enum `{active, closed}`). The default — and the value for any plan
that completed BEFORE this shipped (no signal present) — is `active`
(NOT-closed). Historical plans are NEVER back-filled / auto-closed: absence
of the closed signal degrades gracefully to NOT-closed.

Two things mark a plan closed, written together by `close_orchestrator`:

1. The `_state.json` top-level `lifecycle: "closed"` field (+ `closed_at`
   ISO-8601 timestamp). Additive: root has `additionalProperties: true`
   in `schemas/_state_v1.schema.json`, so this validates with NO version
   bump.
2. The `_orchestrator_closed.lock` sentinel marker file in the plan dir.
   This is the durable idempotency token: its presence means "already
   closed", so a second `all_done` (or a second explicit close) is a
   no-op (the closed-write / audit-append happen AT MOST ONCE).

Two triggers invoke `close_orchestrator`:

- **auto-on-first-`all_done`** — the picker CLI
  (`bin/_orchestrator_query/main.py`) calls this from the exit-23
  (`all_done`) branch. That code path is the SAME `_compute_verdict`
  surface the live `bin/orchestrator-next-ready` picker runs on every
  invocation, so this helper MUST be a safe no-op for any non-`all_done`
  verdict (the caller only invokes it on `all_done`) and MUST NEVER
  raise into the picker (the picker wraps the call best-effort).
- **explicit operator close** — `bin/update_orchestrator --close <slug>`.

The orchestrator JSON (`<slug>_orchestrator.json`) is NEVER mutated by the
close write — only `_state.json` and the `_orchestrator_closed.lock`
marker are touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .state_writer import (
    read_state,
    state_flock,
    state_path,
    write_state,
)


# ---------------------------------------------------------------------- #
# Lifecycle mini-enum (closed set). Source-of-truth for the `lifecycle`
# field's allowed values; the schema declares the field open (additive),
# so this constant is the enforcement seam (mirrors how SEVEN_STATUS is
# the source-of-truth for `tasks[].status`).
# ---------------------------------------------------------------------- #
LIFECYCLE_ACTIVE = "active"
LIFECYCLE_CLOSED = "closed"
LIFECYCLE_VALUES = frozenset({LIFECYCLE_ACTIVE, LIFECYCLE_CLOSED})

# Top-level field name on `_state.json`.
LIFECYCLE_FIELD = "lifecycle"
CLOSED_AT_FIELD = "closed_at"

# Sentinel marker filename inside docs/plans/<slug>/.
CLOSED_LOCK_NAME = "_orchestrator_closed.lock"


class LifecycleValueError(ValueError):
    """A `lifecycle` value outside the `{active, closed}` mini-enum."""


def validate_lifecycle(value: str) -> str:
    """Return `value` if it is a member of the lifecycle mini-enum, else raise.

    The schema leaves the `lifecycle` field open (additive policy), so this
    function is the closed-enum enforcement seam. Any value outside
    `{active, closed}` raises `LifecycleValueError`.
    """
    if value not in LIFECYCLE_VALUES:
        raise LifecycleValueError(
            f"lifecycle={value!r} not in mini-enum; valid: {sorted(LIFECYCLE_VALUES)}"
        )
    return value


def closed_lock_path(plan_dir: Path) -> Path:
    """Path to the `_orchestrator_closed.lock` sentinel marker."""
    return Path(plan_dir) / CLOSED_LOCK_NAME


def lifecycle_of(state: dict) -> str:
    """Resolve the effective lifecycle from a parsed `_state.json` dict.

    Missing / absent / non-string `lifecycle` degrades to `active`
    (NOT-closed). This is the "missing signal → NOT-closed, no back-fill"
    rule: a plan that completed before this shipped has no `lifecycle`
    field and is therefore treated as active.

    A present-but-invalid value (outside the mini-enum) ALSO degrades to
    `active` — the conservative side — rather than raising, so a hand-
    corrupted state file never crashes a reader. (The WRITE seam,
    `validate_lifecycle`, refuses to PERSIST an invalid value.)
    """
    value = state.get(LIFECYCLE_FIELD)
    if not isinstance(value, str):
        return LIFECYCLE_ACTIVE
    if value not in LIFECYCLE_VALUES:
        return LIFECYCLE_ACTIVE
    return value


def is_closed(plan_dir: Path) -> bool:
    """Return True iff the plan is closed.

    A plan is closed iff its `_orchestrator_closed.lock` marker exists. The
    marker is the durable idempotency token (the `_state.json` lifecycle
    field is the queryable mirror; the two are written together). Marker
    presence is authoritative because it is the single artifact the close
    write creates last + checks first.
    """
    return closed_lock_path(plan_dir).exists()


def _now_iso() -> str:
    """ISO-8601 UTC timestamp (second precision, trailing Z-equivalent)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")


@dataclass(frozen=True)
class CloseResult:
    """Outcome of a `close_orchestrator` call.

    Attributes
    ----------
    closed_now : bool
        True iff THIS call performed the closed-write (first close). False
        means the plan was already closed (idempotent no-op).
    already_closed : bool
        True iff the plan was already closed when this call ran.
    closed_at : str or None
        The ISO timestamp stamped on first close (only when
        `closed_now` is True), else None.
    """

    closed_now: bool
    already_closed: bool
    closed_at: Optional[str] = None


def close_orchestrator(
    plan_dir: Path,
    *,
    slug: Optional[str] = None,
    reason: Optional[str] = None,
    trigger: str = "auto_all_done",
    emit_audit: bool = True,
    assume_flock_held: bool = False,
) -> CloseResult:
    """Mark `plan_dir`'s orchestrator closed — idempotent, at-most-once.

    Writes BOTH the `_state.json` `lifecycle: "closed"` field (+ `closed_at`)
    AND the `_orchestrator_closed.lock` sentinel marker. If the marker
    already exists, this is a NO-OP: no second state write, no second audit
    append (idempotency). The orchestrator JSON is NEVER touched.

    Parameters
    ----------
    plan_dir : Path
        `docs/plans/<slug>/` directory.
    slug : str, optional
        Plan slug (for the audit row). Inferred from `plan_dir.name` when
        omitted.
    reason : str, optional
        Free-form reason for the audit row. A default is generated from
        `trigger` when omitted.
    trigger : str
        Provenance discriminator for the audit row (`auto_all_done` for the
        picker path, `operator_close` for the explicit CLI action).
    emit_audit : bool
        When True (default), append a best-effort `orchestrator_closed`
        audit row. Audit failures are swallowed (the close marker +
        `_state.json` write are already durable) so the picker auto-close
        path never raises.
    assume_flock_held : bool
        When True, the caller already holds `state_flock(plan_dir)`; this
        function does the read-modify-write WITHOUT re-acquiring (avoids
        self-deadlock when invoked from inside an existing flock). When
        False (default), it acquires the flock itself.

    Returns
    -------
    CloseResult
        `closed_now=True` on first close, `already_closed=True` on a repeat.

    Notes
    -----
    Never raises for a non-`all_done` situation — the caller decides WHEN to
    close; this function only performs the write. The picker invokes it ONLY
    on the `all_done` verdict and wraps the call best-effort regardless.
    """
    plan_dir = Path(plan_dir)
    resolved_slug = slug or plan_dir.name

    if assume_flock_held:
        return _close_locked(
            plan_dir,
            slug=resolved_slug,
            reason=reason,
            trigger=trigger,
            emit_audit=emit_audit,
        )
    with state_flock(plan_dir):
        return _close_locked(
            plan_dir,
            slug=resolved_slug,
            reason=reason,
            trigger=trigger,
            emit_audit=emit_audit,
        )


def _close_locked(
    plan_dir: Path,
    *,
    slug: str,
    reason: Optional[str],
    trigger: str,
    emit_audit: bool,
) -> CloseResult:
    """Core close RMW; caller MUST hold `state_flock(plan_dir)`."""
    lock_path = closed_lock_path(plan_dir)

    # Idempotency gate: marker present → already closed → no-op.
    if lock_path.exists():
        return CloseResult(closed_now=False, already_closed=True, closed_at=None)

    closed_at = _now_iso()

    # 1. Mutate `_state.json`: additive `lifecycle` + `closed_at`. Read the
    #    current state (may be `{}` if the plan never had a state file).
    state = read_state(plan_dir)
    state[LIFECYCLE_FIELD] = LIFECYCLE_CLOSED
    state[CLOSED_AT_FIELD] = closed_at
    write_state(plan_dir, state)

    # 2. Write the sentinel marker LAST (durable idempotency token; its
    #    presence is what `is_closed` / the gate above check). A small JSON
    #    body records provenance for operator forensics.
    plan_dir.mkdir(parents=True, exist_ok=True)
    marker_body = (
        '{"slug": %r, "closed_at": %r, "trigger": %r}\n'
        % (slug, closed_at, trigger)
    ).replace("'", '"')
    lock_path.write_text(marker_body, encoding="utf-8")

    # 3. Best-effort audit append (never fatal — marker + state already
    #    durable). Routed through the §E lifecycle writer identity.
    if emit_audit:
        _emit_closed_audit(
            plan_dir,
            slug=slug,
            reason=reason or f"orchestrator closed ({trigger})",
            trigger=trigger,
            closed_at=closed_at,
        )

    return CloseResult(closed_now=True, already_closed=False, closed_at=closed_at)


def _emit_closed_audit(
    plan_dir: Path,
    *,
    slug: str,
    reason: str,
    trigger: str,
    closed_at: str,
) -> None:
    """Append a best-effort `orchestrator_closed` audit row.

    Swallows ANY exception: the close marker + `_state.json` write already
    succeeded, so a JSONL-writer hiccup must not fail the close (and MUST
    NOT propagate into the picker auto-close path). The row is stamped with
    the registered `bin/update_orchestrator` writer identity (the lifecycle
    authority) and carries an `orchestrator_closed` `event_type`
    discriminator.
    """
    try:
        from .log_emit import EMIT_BASE, emit_transition

        emit_transition(
            plan_dir=plan_dir,
            plan_slug=slug,
            task_id=None,
            transition_from="unknown",
            transition_to="unknown",
            reason=reason,
            emitted_by=EMIT_BASE,
            extra={
                "event_type": "orchestrator_closed",
                "lifecycle": LIFECYCLE_CLOSED,
                "close_trigger": trigger,
                "closed_at": closed_at,
            },
        )
    except Exception:
        # Intentionally swallowed — see docstring. Audit is advisory; the
        # durable close artifacts (marker + state) are already written.
        return
