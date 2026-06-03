"""Thin delegate for `intent_event_log` writes (§P.impl.11).

`markers.emit(...)` already double-emits stdout + intent_event_log. This
module exposes a slim API for direct emission paths that aren't a
stdout marker (e.g., refusal events logged for measurability per
P.impl.7).

`append_intent_event(...)` validates `emitted_by` via
`refusal.validate_emitted_by(...)`, which reads `refusal.EMITTED_BY`
transitively — allowlist extensions land here automatically. T2
(intent_session_auto_register) extended that allowlist with
`session_start_auto` (KNOWN_WRITERS v5 → v6); the validation surface here
covers it without an explicit additions list.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from . import db, refusal

logger = logging.getLogger(__name__)


def append_intent_event(
    event: str,
    payload: dict[str, Any],
    *,
    emitted_by: str,
    session_id: Optional[str] = None,
    collision_id: Optional[str] = None,
    mysql_conn=None,
    host: Optional[str] = None,
    ts: Optional[str] = None,
) -> None:
    """INSERT one row into intent_event_log. Best-effort — swallows
    MySQL outages but logs them.

    Payload schema (T1 — intent_session_auto_register):
      - `claude_session_id: <str>` MAY be included in the payload when the
        emitting call site has it (e.g., register.py post-T1 with
        --claude-session-id). The field passes through verbatim to the
        `payload` JSON column; intent_event_log itself has no dedicated
        column. Consumers join to `extraction.agent_sessions.claude_session_id`
        via `session_id` when full session context is needed.
    """
    refusal.validate_event(event)
    refusal.validate_emitted_by(emitted_by)
    import socket
    import datetime

    host = host or socket.gethostname()
    ts = ts or (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    own = mysql_conn is None
    try:
        if own:
            mysql_conn = db.connection()
        cursor = mysql_conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO extraction.intent_event_log
                    (event, session_id, collision_id, payload, emitted_by,
                     host, emitted_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    event,
                    session_id,
                    collision_id,
                    json.dumps(payload, sort_keys=True, ensure_ascii=False),
                    emitted_by,
                    host,
                    ts,
                ),
            )
            mysql_conn.commit()
        finally:
            try:
                cursor.close()
            except Exception:  # noqa: BLE001
                pass
    except db.MySQLUnavailable:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("intent_event_log direct emit failed: %s", exc)
    finally:
        if own and mysql_conn is not None:
            try:
                mysql_conn.close()
            except Exception:  # noqa: BLE001
                pass


__all__ = ["append_intent_event"]
