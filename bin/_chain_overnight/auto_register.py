"""Chain-spawn auto-register hook — §P intent registry integration.

Per implplan §A.impl.5b (lines 672-716) — follow-up edit applied pre-flight.
Operator-ratified default: `intent.auto_register_chain_overnight=true`
(per §P.impl.10 line 5023 + §P.impl.17 #2 ratification).

Hook point: called from `bin/_chain_overnight/main.py::main()`
immediately after `manifest.stamp_chain_start` and BEFORE the first
phase-spawn subprocess (the SPAWNING_PHASE transition).

§P NOT yet built — this build's hook is a no-op-or-stub:
- Try to import `bin._intent.api.register_session`.
- On ImportError or any other exception, log a warning to
  `bin/hook-log` (best-effort) and continue. Chain work pre-existed
  §P; degrading to "no auto-register" is preferable to halting on
  observability-layer failures (per §A.impl.5b failure-modes line 702).
- When §P ships, the import will succeed and the call will execute
  normally; the only edit needed at that time is to wire the
  collision-routing behavior described in §A.impl.5b step 5.

Output:
- On success: returns an `AutoRegisterResult` carrying session_id.
- On failure: returns an `AutoRegisterResult` with status="failed_open"
  and a warning attached.
- On settings opt-out: returns status="opt_out", no-op.

Caller (main.py) logs a `chain_session_auto_registered` row via
§C's `append_row(..., emitted_by="chain_driver_auto")` per
§A.impl.5b step 6.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutoRegisterResult:
    """Result of `auto_register_chain_session(...)`.

    `status` enum:
    - `registered` — §P session row inserted; session_id stamped.
    - `opt_out` — settings knob `intent.auto_register_chain_overnight`
      is false; no-op.
    - `failed_open` — §P unavailable (ImportError, MySQL down, etc.);
      warning attached; chain proceeds.
    - `collision_detected` — §P.impl.5 collision; caller halts with
      exit code 40 (interactive) or routes to morning-review
      (autonomous). NOT YET WIRED — §P not built; placeholder for
      future ratification path.
    """

    status: Literal["registered", "opt_out", "failed_open", "collision_detected"]
    session_id: str | None = None
    warning: str | None = None


def _resolve_settings_knob() -> bool:
    """Check `intent.auto_register_chain_overnight` settings knob.

    Per §P.impl.10 + §P.impl.17 #2: default true. §P registry-resolver
    not yet built; consult env var as transitional resolution path:
    `SPLOCK_INTENT_AUTO_REGISTER` (closed enum '0' / 'false' / 'no' to
    opt out).
    """
    raw = os.environ.get("SPLOCK_INTENT_AUTO_REGISTER", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def _build_intent_summary(
    *,
    chain_id: str,
    slug: str,
    mode_flags: tuple[str, ...] = (),
) -> str:
    """Per §A.impl.5b step 2: build the human-readable intent_summary."""
    chain_id_short = chain_id[-8:] if len(chain_id) >= 8 else chain_id
    flags_str = ",".join(mode_flags) if mode_flags else "(none)"
    return (
        f"chain-overnight | slug={slug} | "
        f"chain_id={chain_id_short} | mode_flags={flags_str}"
    )


def _build_closure_trigger(wall_clock_cap_seconds: int) -> str:
    """Per §A.impl.5b step 3: closure trigger driven by chain cap."""
    return f"session_timeout:{wall_clock_cap_seconds}s"


def auto_register_chain_session(
    *,
    chain_id: str,
    slug: str,
    manifest: dict[str, Any],
    mode_flags: tuple[str, ...] = (),
    session_id: str | None = None,
    scope_paths: tuple[str, ...] = (),
) -> AutoRegisterResult:
    """Register the chain in the §P intent registry, or fail open.

    Per implplan §A.impl.5b algorithm:

    1. Check settings knob. If false → no-op (opt_out).
    2. Build intent_summary + closure_trigger from manifest.
    3. Invoke `bin._intent.api.register_session(...)`.
    4. On `intent_collision_detected`: caller routes per §P.impl.6.
    5. On §P unavailable (ImportError): warning + chain proceeds.
    6. On success: caller logs `chain_session_auto_registered` row via
       `emitted_by="chain_driver_auto"` (KNOWN_WRITERS v4).

    Parameters
    ----------
    chain_id : str
        Chain identifier from sentinel.
    slug : str
        Plan slug.
    manifest : dict
        Manifest dict (post `stamp_chain_start`) — source of
        wall_clock_cap_seconds for the closure trigger.
    mode_flags : tuple[str, ...]
        Active mode flags (overnight, guardrail, etc.) — folded into
        intent_summary for forensic value.
    session_id : str | None
        Optional SPLOCK_INTENT_SESSION_ID; defaults to env var or None.
    scope_paths : tuple[str, ...]
        Scope paths the chain will touch (typically `docs/plans/<slug>/`).
    """
    if not _resolve_settings_knob():
        logger.debug(
            "intent.auto_register_chain_overnight opt-out via "
            "SPLOCK_INTENT_AUTO_REGISTER; no-op"
        )
        return AutoRegisterResult(status="opt_out")

    intent_summary = _build_intent_summary(
        chain_id=chain_id, slug=slug, mode_flags=mode_flags,
    )
    closure_trigger = _build_closure_trigger(
        int(manifest.get("wall_clock_cap_seconds", 0))
    )
    resolved_session_id = session_id or os.environ.get("SPLOCK_INTENT_SESSION_ID")

    try:
        # Lazy import — §P not yet built; ImportError on missing module.
        from bin._intent.api import register_session  # type: ignore[import-not-found]
    except ImportError:
        warning = (
            "chain_driver_auto_register_failed reason=ImportError "
            "(bin._intent.api not yet built — §P parallel-track Phase 3 dep)"
        )
        logger.warning(warning)
        return AutoRegisterResult(status="failed_open", warning=warning)

    try:
        result = register_session(
            emitted_by="chain_driver_auto",
            intent_summary=intent_summary,
            closure_trigger=closure_trigger,
            scope_paths=list(scope_paths) if scope_paths else [],
            session_id=resolved_session_id,
            plan_slug=slug,
            chain_id=chain_id,
        )
    except Exception as exc:  # noqa: BLE001 — §P observability layer is fail-open
        warning = (
            f"chain_driver_auto_register_failed reason={type(exc).__name__}: {exc}"
        )
        logger.warning(warning)
        return AutoRegisterResult(status="failed_open", warning=warning)

    # Inspect the result for collision shape.
    if isinstance(result, dict) and result.get("status") == "collision_detected":
        return AutoRegisterResult(
            status="collision_detected",
            session_id=result.get("session_id"),
            warning="intent_collision_detected — caller routes per §P.impl.6",
        )

    # Success path. The §P API may return either a session_id string or
    # a dict with session_id. Tolerant of both shapes.
    session_id_resolved: str | None = None
    if isinstance(result, str):
        session_id_resolved = result
    elif isinstance(result, dict):
        session_id_resolved = result.get("session_id")
    return AutoRegisterResult(
        status="registered", session_id=session_id_resolved,
    )


__all__ = [
    "AutoRegisterResult",
    "auto_register_chain_session",
]
