"""`bin/intent register` subcommand (implplan §P.impl.5).

SERIALIZABLE check-and-insert algorithm (P.impl.5 lines 8642-8688):
  1. `jsonl_writer.append_row(payload)` — local JSONL FIRST.
  2. Open db.serializable_transaction(); SELECT ... FOR UPDATE.
  3. Post-fetch `filter_python_glob_overlap(...)`.
  4. If empty → INSERT + commit + emit `intent.register` + exit 0.
  5. If non-empty → INSERT collision_log + commit + emit `intent.collision`
     + dispatch.handle_collision(...) + exit 40.

Local JSONL row stays in place on collision with `sync_pending=true` +
`collision_outcome="blocked"` per the MINOR-1 clarification (lines
8661-8670). Doctor reconciles to closed_at=<ts> + closure_trigger=
"collision_blocked".

On MySQL outage (db.MySQLUnavailable): local row already written with
sync_pending=true; emit `intent.sync_pending`; exit 0.

T3 (intent_session_auto_register) — env-var fast-paths:
  - SPLOCK_INTENT_COLLISION_HALT_ACTION: highest-precedence override for
    `_resolve_collision_halt_action()`. Honored when SessionStart hook
    invokes `bin/intent register` as a subprocess and needs to force
    `log_only` (per recon §6.11(b)) so the second of two-terminal
    concurrent SessionStart fires never silently drops a session.
  - SPLOCK_INTENT_AUTO_REGISTER_INTERACTIVE: when set to a truthy value
    (e.g. `1`, `true`, `skip_mysql_cold_cache`), register writes the
    local JSONL row with `sync_pending=true` and SKIPS the MySQL leg
    entirely on this invocation. Doctor reconciles later. Sub-1s
    SessionStart hot-path budget enabler.
"""

from __future__ import annotations

import datetime
import fnmatch
import json
import logging
import os
import secrets
import socket
import sys
from typing import Any, Optional

from . import closure_triggers, db, dispatch, jsonl_writer, markers, refusal
from .exit_codes import (
    EXIT_ATOMIC_WRITE_FAILED,
    EXIT_ENUM_VIOLATION,
    EXIT_INTENT_CLOSURE_TRIGGER_OPEN_ENDED,
    EXIT_INTENT_COLLISION_DETECTED,
    EXIT_OK,
)

logger = logging.getLogger(__name__)


def _now_iso_z() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def new_session_id() -> str:
    """`sess_YYYY-MM-DDTHH:MM:SSZ_<random4>` per the §I env-var registry
    regex (SPLOCK_INTENT_SESSION_ID)."""
    ts = _now_iso_z()
    suffix = secrets.token_hex(2)  # 4 hex chars
    return f"sess_{ts}_{suffix}"


def new_collision_id() -> str:
    ts = _now_iso_z()
    suffix = secrets.token_hex(3)
    return f"col_{ts}_{suffix}"


def filter_python_glob_overlap(
    matches: list[dict],
    requested_paths: list[str],
) -> list[dict]:
    """Post-fetch filter — SQL caught exact + JSON_OVERLAPS; this catches
    glob ∩ glob overlap (the plan §P.5 caveat).

    A match is kept if any of its claimed_paths globs overlaps any
    requested path glob. `fnmatch` is the glob primitive; overlap is
    detected by:
      - exact string equality
      - the requested glob matching the literal claimed glob (or vice-versa)
      - either glob matching a probe string built from the other
    """
    if not matches:
        return []
    out: list[dict] = []
    for m in matches:
        cps = m.get("claimed_paths")
        if isinstance(cps, str):
            try:
                cps = json.loads(cps)
            except json.JSONDecodeError:
                cps = []
        if not isinstance(cps, list):
            cps = []
        for claimed in cps:
            if _globs_overlap(claimed, requested_paths):
                out.append(m)
                break
    return out


def _globs_overlap(claimed: str, requested: list[str]) -> bool:
    for r in requested:
        if r == claimed:
            return True
        # Treat each glob as a pattern; check if a probe derived from
        # one is matched by the other.
        if fnmatch.fnmatch(claimed, r):
            return True
        if fnmatch.fnmatch(r, claimed):
            return True
        # Prefix containment (common case: dir/** overlaps dir/sub/**).
        c_prefix = claimed.split("*", 1)[0].rstrip("/")
        r_prefix = r.split("*", 1)[0].rstrip("/")
        if c_prefix and r_prefix:
            if c_prefix.startswith(r_prefix) or r_prefix.startswith(c_prefix):
                return True
    return False


def _build_local_row(
    *,
    session_id: str,
    kind: str,
    target_system_area: str,
    claimed_paths: list[str],
    proposed_design_pattern: Optional[str],
    closure_trigger: str,
    originating_chain_id: Optional[str],
    originating_plan_slug: Optional[str],
    emitted_by: str,
    host: str,
    ts: str,
    claude_session_id: Optional[str] = None,
) -> dict:
    return {
        "session_id": session_id,
        "kind": kind,
        "target_system_area": target_system_area,
        "claimed_paths": list(claimed_paths),
        "proposed_design_pattern": proposed_design_pattern,
        "status": "Planning",
        "closure_trigger": closure_trigger,
        "originating_chain_id": originating_chain_id,
        "originating_plan_slug": originating_plan_slug,
        "host": host,
        "started_at": ts,
        "last_activity_at": ts,
        "closed_at": None,
        "emitted_by": emitted_by,
        "sync_pending": False,
        # T1 (intent_session_auto_register): side-column capture of the
        # Claude Code session_id from the SessionStart hook envelope.
        # NULL for chain-overnight + pre-T1 rows.
        "claude_session_id": claude_session_id,
    }


# T1 (intent_session_auto_register): `claude_session_id` shape validation.
# Closed-set: ASCII printable, no control chars, len <= 64 (DB column width).
# Anthropic session_ids are UUIDv4 (36 chars); we cap at 64 to leave room
# for any future suffix while still fitting the VARCHAR(64) column.
CLAUDE_SESSION_ID_MAX_LEN = 64


def _validate_claude_session_id(value: Optional[str]) -> Optional[str]:
    """Return normalized value or raise ValueError on malformed input.

    None / empty-string → None (column stays NULL).
    Rejects: empty string after strip, control chars (< 0x20 or 0x7f),
    or > 64 chars. Returns the stripped string otherwise.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"claude_session_id must be str, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        # Treat blank as "not provided" → NULL.
        return None
    if len(stripped) > CLAUDE_SESSION_ID_MAX_LEN:
        raise ValueError(
            f"claude_session_id length {len(stripped)} exceeds max "
            f"{CLAUDE_SESSION_ID_MAX_LEN}"
        )
    for ch in stripped:
        codepoint = ord(ch)
        if codepoint < 0x20 or codepoint == 0x7F:
            raise ValueError(
                f"claude_session_id contains control character "
                f"(U+{codepoint:04X})"
            )
    return stripped


_VALID_HALT_ACTIONS = ("halt", "warn", "log_only")


def _resolve_collision_halt_action() -> str:
    """Resolve `intent.collision_halt_action` at runtime.

    Resolution order (T3 — intent_session_auto_register adds the env-var
    fast-path at the top of this ladder):

    1. **`SPLOCK_INTENT_COLLISION_HALT_ACTION` env var** — if set to a
       value in the closed set `{halt, warn, log_only}`, that value
       wins. Used by the SessionStart hook subprocess invocation to
       force `log_only` (recon §6.11(b)) so two-terminal concurrent
       SessionStart fires never silently drop a session.
    2. `settings.resolve("intent.collision_halt_action", default="halt")` —
       the framework-internal resolver's §P.impl.10 knob.
    3. Static fallback `"halt"` when the resolver lookup fails or the
       knob is not yet seeded — preserving the plan-explicit default
       behavior.

    Unknown env-var values are ignored (fall through to step 2 + 3)
    rather than raising, matching the existing "if halt_action not in
    valid → fallback to halt" defensive pattern in `run()`.
    """
    env_value = os.environ.get("SPLOCK_INTENT_COLLISION_HALT_ACTION", "").strip()
    if env_value and env_value in _VALID_HALT_ACTIONS:
        return env_value
    try:
        from . import settings as _settings
        return _settings.resolve(
            "intent.collision_halt_action", default="halt"
        )
    except Exception:  # noqa: BLE001
        return "halt"


def _skip_mysql_on_cold_cache() -> bool:
    """T3 (intent_session_auto_register) — env-var fast-path.

    When `SPLOCK_INTENT_AUTO_REGISTER_INTERACTIVE` is set to a truthy value,
    register writes the local JSONL row with `sync_pending=true` and
    SKIPS the MySQL leg entirely. Doctor reconciles later. This is the
    sub-1s SessionStart hot-path budget enabler — without it, every
    SessionStart fork pays the MySQL cold-cache connect cost.

    Truthy values: `1`, `true`, `yes`, `skip_mysql_cold_cache` (case
    insensitive). All other values (including unset, empty, `0`,
    `false`, `no`) → False.
    """
    raw = os.environ.get("SPLOCK_INTENT_AUTO_REGISTER_INTERACTIVE", "").strip().lower()
    if not raw:
        return False
    return raw in ("1", "true", "yes", "skip_mysql_cold_cache")


def run(
    *,
    area: str,
    paths: list[str],
    kind: str,
    closure: Optional[str] = None,
    design_pattern: Optional[str] = None,
    plan: Optional[str] = None,
    chain_id: Optional[str] = None,
    emitted_by: str = "bin/intent:register",
    ttl_minutes: int = 240,
    dry_run: bool = False,
    json_output: bool = False,
    claude_session_id: Optional[str] = None,
    upsert: bool = False,
    stdout=None,
    stderr=None,
) -> int:
    """Execute the register algorithm. Returns the exit code.

    `claude_session_id` (T1 — intent_session_auto_register): optional
    Claude Code session_id from the SessionStart hook envelope; stored
    as a side column for `/clear`-recovery lookups via
    `bin/intent list --claude-session <id>`. Validated for shape +
    length per `_validate_claude_session_id`; malformed values map to
    EXIT_USAGE (1) rather than 500-ing.

    `upsert`: when True AND `claude_session_id` is set AND an open row
    already exists for it, bumps that row's last_activity_at to NOW
    and returns EXIT_OK without inserting. Enables the UserPromptSubmit
    hook to fire on every prompt without creating row spam.
    """
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    halt_action = _resolve_collision_halt_action()
    # --- Upsert fast-path: bump last_activity if open row exists ----------
    if upsert and claude_session_id:
        try:
            from . import db as _db
            with _db.connection() as _conn:
                _cur = _conn.cursor(dictionary=True)
                _cur.execute(
                    "SELECT session_id FROM extraction.agent_sessions "
                    "WHERE claude_session_id = %s AND closed_at IS NULL "
                    "ORDER BY started_at DESC LIMIT 1",
                    (claude_session_id,),
                )
                _row = _cur.fetchone()
                if _row:
                    _cur.execute(
                        "UPDATE extraction.agent_sessions "
                        "SET last_activity_at = UTC_TIMESTAMP() "
                        "WHERE session_id = %s",
                        (_row["session_id"],),
                    )
                    _conn.commit()
                    if json_output:
                        out.write(json.dumps(
                            {"ok": True, "upsert": "touched",
                             "session_id": _row["session_id"]},
                            sort_keys=True) + "\n")
                    return EXIT_OK
        except _db.MySQLUnavailable:
            # Fall through to the normal register path; that path's
            # fast-path will land the row in local JSONL with sync_pending.
            pass
    if halt_action not in ("halt", "warn", "log_only"):
        halt_action = "halt"

    # --- T4 (intent_session_auto_register): env-var fast-paths -----------
    # Per research Decision 2. Resolution order is env > CLI > sentinel:
    #   1. SPLOCK_INTENT_AREA env var → wins over the CLI --area flag.
    #   2. `area` argument (the CLI value when supplied) — preserved when
    #      the env var is unset.
    #   3. SENTINEL_AREA ("unscoped_interactive") when neither is set.
    # Same precedence for SPLOCK_INTENT_SUMMARY → design_pattern.
    # Routed through bin/_intent/settings.resolve_area_from_env /
    # resolve_summary_from_env so the sentinel literal stays centralized.
    from . import settings as intent_settings
    area = intent_settings.resolve_area_from_env(cli_value=area)
    design_pattern = intent_settings.resolve_summary_from_env(
        cli_value=design_pattern
    )

    # --- Closed-enum validation ------------------------------------------
    try:
        refusal.validate_kind(kind)
        refusal.validate_emitted_by(emitted_by)
    except refusal.EnumViolation as exc:
        err.write(json.dumps({"error": "enum_violation", "detail": str(exc)}) + "\n")
        return EXIT_ENUM_VIOLATION

    # --- T1: claude_session_id shape validation --------------------------
    try:
        claude_session_id = _validate_claude_session_id(claude_session_id)
    except ValueError as exc:
        err.write(
            json.dumps(
                {"error": "claude_session_id_invalid", "detail": str(exc)}
            )
            + "\n"
        )
        from .exit_codes import EXIT_USAGE
        return EXIT_USAGE

    # --- Closure-trigger parse + open-ended refusal ----------------------
    raw_closure = closure or closure_triggers.default_session_timeout(ttl_minutes)
    try:
        closure_spec = closure_triggers.parse(raw_closure)
    except closure_triggers.OpenEndedClosureTriggerError as exc:
        err.write(
            json.dumps(
                {
                    "error": "intent_closure_trigger_open_ended",
                    "detail": str(exc),
                    "plan_citation": (
                        "§P.impl.7 + research_findings_v1.md §D"
                    ),
                }
            )
            + "\n"
        )
        # Best-effort measurability log per P.impl.7.
        try:
            from . import log_emit
            log_emit.append_intent_event(
                "intent.register",  # closest existing event; payload carries refusal
                {
                    "refusal": "intent_closure_trigger_open_ended",
                    "raw_closure": raw_closure,
                    "area": area,
                },
                emitted_by=emitted_by,
            )
        except Exception:  # noqa: BLE001
            pass
        return EXIT_INTENT_CLOSURE_TRIGGER_OPEN_ENDED
    except closure_triggers.MalformedClosureTriggerError as exc:
        err.write(json.dumps({"error": "enum_violation", "detail": str(exc)}) + "\n")
        return EXIT_ENUM_VIOLATION

    session_id = new_session_id()
    host = socket.gethostname()
    ts = _now_iso_z()

    local_row = _build_local_row(
        session_id=session_id,
        kind=kind,
        target_system_area=area,
        claimed_paths=paths,
        proposed_design_pattern=design_pattern,
        closure_trigger=closure_spec.raw,
        originating_chain_id=chain_id,
        originating_plan_slug=plan,
        emitted_by=emitted_by,
        host=host,
        ts=ts,
        claude_session_id=claude_session_id,
    )

    if dry_run:
        out.write(
            json.dumps(
                {
                    "dry_run": True,
                    "would_write_local": local_row,
                    "closure_trigger": closure_spec.raw,
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n"
        )
        return EXIT_OK

    # --- Step 1: local JSONL FIRST ---------------------------------------
    try:
        jsonl_writer.append_row(dict(local_row))
    except OSError as exc:
        err.write(
            json.dumps({"error": "atomic_write_failed", "detail": str(exc)}) + "\n"
        )
        return EXIT_ATOMIC_WRITE_FAILED

    # --- T3 fast-path: skip MySQL on cold cache --------------------------
    # SessionStart hook sub-1s budget enabler (recon cross-cutting
    # constraint). When SPLOCK_INTENT_AUTO_REGISTER_INTERACTIVE is set,
    # mark the local row sync_pending=True + emit the sync_pending
    # marker, then return EXIT_OK without touching MySQL. Doctor
    # reconciles later.
    if _skip_mysql_on_cold_cache():
        _mark_local_sync_pending(
            session_id, error="skip_mysql_cold_cache_fast_path"
        )
        markers.emit(
            markers.EVENT_SYNC_PENDING,
            {
                "session_id": session_id,
                "error_class": "FastPathSkip",
                "error_detail": "SPLOCK_INTENT_AUTO_REGISTER_INTERACTIVE",
            },
            emitted_by=emitted_by,
            session_id=session_id,
        )
        if json_output:
            out.write(
                json.dumps(
                    {
                        "ok": True,
                        "session_id": session_id,
                        "sync_pending": True,
                        "reason": "skip_mysql_cold_cache_fast_path",
                    },
                    sort_keys=True,
                )
                + "\n"
            )
        else:
            out.write(
                f"registered {session_id} (sync_pending: fast-path)\n"
            )
        return EXIT_OK

    # --- Step 2-5: SERIALIZABLE check + INSERT or collision -------------
    try:
        with db.serializable_transaction() as (conn, cursor):
            paths_json = json.dumps(paths, sort_keys=True, ensure_ascii=False)
            matches = db.select_overlapping_for_update(cursor, area, paths_json)
            colliding = filter_python_glob_overlap(matches, paths)

            if not colliding:
                # No collision — INSERT real row + commit.
                db_payload = dict(local_row)
                db_payload["claimed_paths"] = paths_json
                db_payload.pop("sync_pending", None)
                # T1: pop claude_session_id before the canonical INSERT
                # (db.insert_session column list pre-dates the side column);
                # land it via UPDATE within the same SERIALIZABLE txn so
                # the row is committed atomically with the side-column data.
                db_payload.pop("claude_session_id", None)
                db.insert_session(cursor, db_payload)
                if claude_session_id is not None:
                    cursor.execute(
                        "UPDATE extraction.agent_sessions "
                        "SET claude_session_id = %s WHERE session_id = %s",
                        (claude_session_id, session_id),
                    )
                conn.commit()

                # Mark local JSONL row sync_pending=False (already False),
                # then emit marker.
                marker_payload = {
                    "session_id": session_id,
                    "area": area,
                    "kind": kind,
                    "claimed_paths": paths,
                    "host": host,
                    "closure_trigger": closure_spec.raw,
                    "proposed_design_pattern": design_pattern,
                    "started_at": ts,
                }
                if claude_session_id is not None:
                    marker_payload["claude_session_id"] = claude_session_id
                markers.emit(
                    markers.EVENT_REGISTER,
                    marker_payload,
                    emitted_by=emitted_by,
                    session_id=session_id,
                    mysql_conn=conn,
                )

                if json_output:
                    out.write(
                        json.dumps(
                            {
                                "ok": True,
                                "session_id": session_id,
                                "area": area,
                                "kind": kind,
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    )
                else:
                    out.write(f"registered {session_id} (area={area})\n")
                return EXIT_OK

            # --- Collision path ----------------------------------------
            collision_id = new_collision_id()
            mode = "autonomous" if dispatch.is_autonomous_context(chain_id) else "interactive"
            lineage = [
                {
                    "session_id": m.get("session_id"),
                    "kind": m.get("kind"),
                    "target_system_area": m.get("target_system_area"),
                    "claimed_paths": _coerce_paths(m.get("claimed_paths")),
                    "host": m.get("host"),
                    "last_activity_at": _iso(m.get("last_activity_at")),
                }
                for m in colliding
            ]

            # T4 (intent_session_auto_register): sentinel-area skip.
            # When both sides of the collision carry the sentinel
            # `unscoped_interactive` area AND the knob is on (default),
            # promote `halt_action` to "log_only" so the second session
            # proceeds AND the collision_log row is still written for
            # forensic visibility (audit trail). Per research Decision 2.
            from . import settings as intent_settings
            if (
                refusal.collision_is_sentinel_pair(area, lineage)
                and intent_settings.resolve_sentinel_area_skip_collision()
            ):
                halt_action = "log_only"

            db.insert_collision(
                cursor,
                {
                    "collision_id": collision_id,
                    "colliding_session_id": session_id,
                    "colliding_area": area,
                    "lineage_snapshot": json.dumps(
                        lineage, sort_keys=True, ensure_ascii=False
                    ),
                    "dispatch_mode": mode,
                    "resolution": None,
                    "resolution_at": None,
                    "detected_at": ts,
                    "host": host,
                },
            )

            # Per §P.impl.10 `intent.collision_halt_action` knob: warn /
            # log_only modes ALSO insert the agent_sessions row alongside
            # the collision_log row, allowing the colliding session to
            # proceed. halt mode (default) skips the session INSERT.
            if halt_action in ("warn", "log_only"):
                db_payload = dict(local_row)
                db_payload["claimed_paths"] = paths_json
                db_payload.pop("sync_pending", None)
                db_payload.pop("claude_session_id", None)
                db.insert_session(cursor, db_payload)
                if claude_session_id is not None:
                    cursor.execute(
                        "UPDATE extraction.agent_sessions "
                        "SET claude_session_id = %s WHERE session_id = %s",
                        (claude_session_id, session_id),
                    )

            conn.commit()

            markers.emit(
                markers.EVENT_COLLISION,
                {
                    "collision_id": collision_id,
                    "colliding_session_id": session_id,
                    "colliding_area": area,
                    "lineage_snapshot": lineage,
                    "dispatch_mode": mode,
                    "halt_action": halt_action,
                },
                emitted_by=emitted_by,
                session_id=session_id,
                collision_id=collision_id,
                mysql_conn=conn,
            )

            if halt_action == "halt":
                # Mark local JSONL row collision_blocked + sync_pending=True
                # per MINOR-1 clarification.
                _mark_local_collision_blocked(session_id)

                dispatch.handle_collision(
                    mode=mode,
                    collision_id=collision_id,
                    colliding_session_id=session_id,
                    colliding_area=area,
                    lineage_snapshot=lineage,
                    plan_slug=plan,
                    chain_id=chain_id,
                    stderr=err,
                )

                if json_output:
                    out.write(
                        json.dumps(
                            {
                                "collision": True,
                                "collision_id": collision_id,
                                "colliding_session_id": session_id,
                                "dispatch_mode": mode,
                                "halt_action": halt_action,
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    )
                return EXIT_INTENT_COLLISION_DETECTED

            # warn / log_only path: session proceeded. Emit a register
            # marker so downstream observability captures the (degraded)
            # successful registration; log_only suppresses the stderr
            # collision payload.
            warn_marker_payload = {
                "session_id": session_id,
                "area": area,
                "kind": kind,
                "claimed_paths": paths,
                "host": host,
                "closure_trigger": closure_spec.raw,
                "proposed_design_pattern": design_pattern,
                "started_at": ts,
                "collision_id": collision_id,
                "halt_action": halt_action,
            }
            if claude_session_id is not None:
                warn_marker_payload["claude_session_id"] = claude_session_id
            markers.emit(
                markers.EVENT_REGISTER,
                warn_marker_payload,
                emitted_by=emitted_by,
                session_id=session_id,
                mysql_conn=conn,
            )
            if halt_action == "warn":
                err.write(
                    json.dumps(
                        {
                            "warning": "intent_collision_detected",
                            "collision_id": collision_id,
                            "colliding_session_id": session_id,
                            "dispatch_mode": mode,
                            "halt_action": "warn",
                        }
                    )
                    + "\n"
                )
            if json_output:
                out.write(
                    json.dumps(
                        {
                            "ok": True,
                            "session_id": session_id,
                            "collision_id": collision_id,
                            "halt_action": halt_action,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            else:
                out.write(
                    f"registered {session_id} (collision={collision_id}; "
                    f"halt_action={halt_action})\n"
                )
            return EXIT_OK

    except db.MySQLUnavailable as exc:
        # Local row already landed; flip sync_pending + emit marker.
        _mark_local_sync_pending(session_id, error=str(exc))
        markers.emit(
            markers.EVENT_SYNC_PENDING,
            {
                "session_id": session_id,
                "error_class": "MySQLUnavailable",
                "error_detail": str(exc),
            },
            emitted_by=emitted_by,
            session_id=session_id,
        )
        if json_output:
            out.write(
                json.dumps(
                    {
                        "ok": True,
                        "session_id": session_id,
                        "sync_pending": True,
                        "reason": str(exc),
                    },
                    sort_keys=True,
                )
                + "\n"
            )
        else:
            out.write(
                f"registered {session_id} (sync_pending: {exc})\n"
            )
        return EXIT_OK


def _iso(val) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, datetime.datetime):
        return val.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(val)


def _coerce_paths(raw) -> list[str]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
            return v if isinstance(v, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _mark_local_sync_pending(session_id: str, *, error: str) -> None:
    rows = jsonl_writer.read_all()
    for r in rows:
        if r.get("session_id") == session_id:
            r["sync_pending"] = True
            r["sync_pending_error"] = error
    jsonl_writer.rewrite_all(rows)


def _mark_local_collision_blocked(session_id: str) -> None:
    rows = jsonl_writer.read_all()
    for r in rows:
        if r.get("session_id") == session_id:
            r["sync_pending"] = True
            r["collision_outcome"] = "blocked"
    jsonl_writer.rewrite_all(rows)


__all__ = [
    "run",
    "new_session_id",
    "new_collision_id",
    "filter_python_glob_overlap",
]
