"""Framework-internal settings resolver + intent-knob shims.

Replaces a host ``settings_registry`` import with a zero-dependency
resolver suitable for a fresh-repo install. No host DAL, no MySQL pool,
no ``console/`` import at runtime.

Resolution layers (highest precedence first):

  1. **Env var override** — every knob accepts an env-var override
     under the ``SPLOCK_SETTING__`` prefix with ``.`` rewritten as
     ``__``. Example: ``intent.ttl_minutes`` →
     ``SPLOCK_SETTING__intent__ttl_minutes``.

  2. **JSON overlay file** — operator-tunable JSON dict at
     ``${CLAUDE_PLUGIN_DATA}/intent_settings.json`` (or the location
     returned by :func:`overlay_path`). Five-minute in-process cache
     mirrors the original ``console.settings_registry`` TTL so
     subprocess workers pick up changes within one TTL window.

  3. **Documented default** — the literal passed in by the call site
     (the source-of-truth value per CLAUDE.md "default literal is the
     source of truth at the call site").

The resolver NEVER raises into the call site: any lookup failure
(missing file, malformed JSON, type coercion error) silently falls
through to the next layer. Mirrors the defensive pattern in
``bin/_intent`` callers that previously caught ``Exception`` around
``console + src.DAL`` imports.

T4 intent-session-auto-register knob shims (``resolve_auto_register_*``
etc.) are preserved unchanged at the bottom; they now route through
this module's local :func:`resolve` instead of the host
``console.settings_registry`` + ``src.DAL.DAL.from_pool``.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import threading
import time
from typing import Any, Optional, TypeVar

from . import db

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Env-var override layer
# ---------------------------------------------------------------------------

ENV_PREFIX = "SPLOCK_SETTING__"


def _env_var_name(knob_name: str) -> str:
    """Translate ``a.b.c`` → ``SPLOCK_SETTING__a__b__c``."""
    return ENV_PREFIX + knob_name.replace(".", "__")


def _coerce(raw: str, default: T) -> T:
    """Coerce env-var string to match ``default``'s type.

    Bool: ``1/true/yes/on`` → True; ``0/false/no/off`` → False.
    Int: ``int(raw)``. Float: ``float(raw)``. Other: returned as-is.
    Any coercion failure returns the default unchanged.
    """
    if isinstance(default, bool):
        v = raw.strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True  # type: ignore[return-value]
        if v in ("0", "false", "no", "off"):
            return False  # type: ignore[return-value]
        return default
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(raw)  # type: ignore[return-value]
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(raw)  # type: ignore[return-value]
        except (TypeError, ValueError):
            return default
    return raw  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# JSON overlay layer
# ---------------------------------------------------------------------------

OVERLAY_FILE_NAME = "intent_settings.json"
CACHE_TTL_SECONDS = 300  # mirror console.settings_registry's 5-minute TTL

_cache_lock = threading.Lock()
_cache: dict[pathlib.Path, tuple[float, dict]] = {}


def overlay_path(data_root: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Resolve the JSON overlay file location.

    Co-located with the SQLite db + JSONL mirror under the same
    data-root so the seal-glob lockstep (SC-C #5) covers all three.
    """
    return db.resolve_data_root(data_root) / OVERLAY_FILE_NAME


def _read_overlay(path: pathlib.Path) -> dict:
    """TTL-cached JSON load; never raises into caller."""
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(path)
        if cached is not None and (now - cached[0]) < CACHE_TTL_SECONDS:
            return cached[1]
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        data: dict = {}
    else:
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                data = {}
        except json.JSONDecodeError:
            data = {}
    with _cache_lock:
        _cache[path] = (now, data)
    return data


def invalidate_cache(path: Optional[pathlib.Path] = None) -> None:
    """Drop the TTL-cached overlay (test hook + operator escape hatch)."""
    with _cache_lock:
        if path is None:
            _cache.clear()
        else:
            _cache.pop(path, None)


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------


def resolve(
    knob_name: str,
    default: T,
    *,
    data_root: Optional[pathlib.Path] = None,
) -> T:
    """Resolve a knob's effective value, defensively.

    See module docstring for the layer ordering. Never raises; always
    returns ``default`` on the unhappiest path.
    """
    # Layer 1: env-var override.
    env_key = _env_var_name(knob_name)
    env_value = os.environ.get(env_key)
    if env_value is not None and env_value.strip() != "":
        env_value = env_value.strip()
        try:
            return _coerce(env_value, default)
        except Exception:  # noqa: BLE001 — never raise from resolver
            logger.debug(
                "[splock.intent.settings.resolve] env coercion failed "
                "for %s=%r; falling through",
                env_key, env_value, exc_info=True,
            )

    # Layer 2: JSON overlay file.
    try:
        overlay = _read_overlay(overlay_path(data_root))
    except Exception:  # noqa: BLE001
        overlay = {}
    if knob_name in overlay:
        try:
            value = overlay[knob_name]
            if isinstance(default, bool):
                return bool(value)  # type: ignore[return-value]
            if isinstance(default, int) and not isinstance(default, bool):
                return int(value)  # type: ignore[return-value]
            if isinstance(default, float):
                return float(value)  # type: ignore[return-value]
            return value  # type: ignore[return-value]
        except Exception:  # noqa: BLE001
            logger.debug(
                "[splock.intent.settings.resolve] overlay coercion failed "
                "for %s=%r; returning default",
                knob_name, overlay.get(knob_name), exc_info=True,
            )

    return default


# ---------------------------------------------------------------------------
# Sentinel constants (T4 — preserved from the pre-extraction module)
# ---------------------------------------------------------------------------

SENTINEL_AREA = "unscoped_interactive"
"""Default ``target_system_area`` for SessionStart auto-registered
interactive sessions when ``SPLOCK_INTENT_AREA`` is unset."""

SENTINEL_SUMMARY = "empty_session"
"""Default ``proposed_design_pattern`` / intent_summary placeholder for
SessionStart auto-registered sessions when ``SPLOCK_INTENT_SUMMARY`` is
unset."""

# Documented defaults — passed in as `default` to the resolver so a
# missing overlay row keeps the call site alive.
_AUTO_REGISTER_DEFAULT = True
_SENTINEL_SKIP_COLLISION_DEFAULT = True
_WARN_ON_UNSCOPED_DEFAULT = False


def _resolve_bool(knob_name: str, default: bool) -> bool:
    """Back-compat shim — routes through the framework-internal resolver."""
    return bool(resolve(knob_name, default))


def resolve_auto_register_interactive_session() -> bool:
    """``intent.auto_register_interactive_session`` — master switch."""
    return _resolve_bool(
        "intent.auto_register_interactive_session",
        _AUTO_REGISTER_DEFAULT,
    )


def resolve_sentinel_area_skip_collision() -> bool:
    """``intent.sentinel_area_skip_collision`` — sentinel-skip gate."""
    return _resolve_bool(
        "intent.sentinel_area_skip_collision",
        _SENTINEL_SKIP_COLLISION_DEFAULT,
    )


def resolve_warn_on_unscoped_session() -> bool:
    """``intent.warn_on_unscoped_session`` — case-2 soft-warn gate."""
    return _resolve_bool(
        "intent.warn_on_unscoped_session",
        _WARN_ON_UNSCOPED_DEFAULT,
    )


# ---------------------------------------------------------------------------
# Env-var fast-paths (T4 — SPLOCK_INTENT_AREA + SPLOCK_INTENT_SUMMARY)
# ---------------------------------------------------------------------------
#
# Per research Decision 2: SessionStart-auto-register reads env vars for
# the area + summary placeholders. Precedence order:
#
#   1. env var (SPLOCK_INTENT_AREA / SPLOCK_INTENT_SUMMARY)
#   2. CLI flag (--area / --design-pattern, when explicitly supplied)
#   3. sentinel default (SENTINEL_AREA / SENTINEL_SUMMARY)
#
# Step 2 is enforced at the caller (register.run); this module is
# responsible only for steps 1 and 3.


def _env_first(*candidates: str) -> str:
    for name in candidates:
        v = os.environ.get(name, "").strip()
        if v:
            return v
    return ""


def resolve_area_from_env(cli_value: Optional[str] = None) -> str:
    """Resolve the effective ``target_system_area`` for SessionStart auto-register."""
    env_value = _env_first("SPLOCK_INTENT_AREA")
    if env_value:
        return env_value
    if cli_value:
        return cli_value
    return SENTINEL_AREA


def resolve_summary_from_env(cli_value: Optional[str] = None) -> str:
    """Resolve the effective intent-summary / design-pattern placeholder."""
    env_value = _env_first("SPLOCK_INTENT_SUMMARY")
    if env_value:
        return env_value
    if cli_value:
        return cli_value
    return SENTINEL_SUMMARY


__all__ = [
    "ENV_PREFIX",
    "OVERLAY_FILE_NAME",
    "CACHE_TTL_SECONDS",
    "SENTINEL_AREA",
    "SENTINEL_SUMMARY",
    "overlay_path",
    "invalidate_cache",
    "resolve",
    "resolve_auto_register_interactive_session",
    "resolve_sentinel_area_skip_collision",
    "resolve_warn_on_unscoped_session",
    "resolve_area_from_env",
    "resolve_summary_from_env",
]
