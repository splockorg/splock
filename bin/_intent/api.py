"""`bin._intent.api` — public Python API consumed by §A chain-spawn
auto-register (`bin/_chain_overnight/auto_register.py`).

Per implplan §P.impl.16 dependencies: the chain driver invokes
`api.register_session(...)` at chain spawn when `intent.auto_register_chain_overnight`
is true. This wraps the same SERIALIZABLE check-and-insert algorithm
that `bin/intent register` runs, returning a dict or session_id string
so the chain driver can detect collisions per A.impl.5b step 5.
"""

from __future__ import annotations

import io
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def register_session(
    *,
    emitted_by: str,
    intent_summary: str,
    closure_trigger: str,
    scope_paths: Optional[list[str]] = None,
    session_id: Optional[str] = None,
    kind: str = "chain_overnight",
    plan_slug: Optional[str] = None,
    chain_id: Optional[str] = None,
) -> dict[str, Any]:
    """Register a chain-overnight session. Returns:

      - {"status": "registered", "session_id": "..."} on success
      - {"status": "collision_detected", "session_id": "...", "collision_id": "..."}
        on collision
      - {"status": "failed", "error": "..."} on hard failure

    Matches the shape expected by `bin/_chain_overnight/auto_register.py`.

    `scope_paths` is the list of globs the chain plans to touch;
    typically `["docs/plans/<slug>/**"]`. Defaults to a single
    plan-dir glob when `plan_slug` is supplied; empty otherwise.
    """
    from . import register as register_mod

    paths = list(scope_paths or [])
    if not paths and plan_slug:
        paths = [f"docs/plans/{plan_slug}/**"]

    area = f"chain:{plan_slug}" if plan_slug else "chain:unknown"
    out = io.StringIO()
    err = io.StringIO()
    rc = register_mod.run(
        area=area,
        paths=paths or [area],
        kind=kind,
        closure=closure_trigger,
        design_pattern=intent_summary,
        plan=plan_slug,
        chain_id=chain_id,
        emitted_by=emitted_by,
        ttl_minutes=240,
        dry_run=False,
        json_output=True,
        stdout=out,
        stderr=err,
    )
    raw = (out.getvalue() or err.getvalue()).strip()
    payload: dict[str, Any] = {}
    if raw:
        import json
        # Find last JSON line — some flows emit multiple stdout lines.
        for line in raw.splitlines()[::-1]:
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

    if rc == 40:
        return {
            "status": "collision_detected",
            "session_id": payload.get("colliding_session_id"),
            "collision_id": payload.get("collision_id"),
            "dispatch_mode": payload.get("dispatch_mode"),
        }
    if rc == 0:
        return {
            "status": "registered",
            "session_id": payload.get("session_id") or session_id,
        }
    return {
        "status": "failed",
        "exit_code": rc,
        "stderr": err.getvalue(),
    }


__all__ = ["register_session"]
