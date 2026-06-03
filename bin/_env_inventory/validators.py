"""Per-var validation helpers (implplan §I.impl.2).

Range / enum / model-pin-format checks. Most validation lives in
`propagation.py::_cast_and_validate`; this module exposes the building
blocks for tests + future extensions (e.g., enum widening) without
re-importing `propagation` internals.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from bin._env_inventory.propagation import MODEL_PIN_RE, InvalidEnvValue


def validate_model_pin(name: str, value: str) -> None:
    """Validate a model-pin string against MODEL_PIN_RE.

    Bare aliases (`opus`, `claude-opus-4-7`, `Sonnet 4.6`, `haiku`) are
    refused with citation to Finding 17 + research_findings_v1.md §I.8.
    """
    if not MODEL_PIN_RE.match(value):
        raise InvalidEnvValue(
            f"{name}={value!r}: bare aliases refused — model IDs must match "
            f"{MODEL_PIN_RE.pattern!r} (Finding 17 + research_findings_v1.md §I.8). "
            f"Use date-stamped form e.g. 'claude-opus-4-7-20260517'."
        )


def validate_range(name: str, value: float, lo: Optional[float], hi: Optional[float]) -> None:
    """Inclusive-range check; raises `InvalidEnvValue` on breach."""
    if lo is not None and value < lo:
        raise InvalidEnvValue(
            f"{name}={value}: below min {lo} (valid range {lo}..{hi if hi is not None else 'unbounded'})"
        )
    if hi is not None and value > hi:
        raise InvalidEnvValue(
            f"{name}={value}: above max {hi} (valid range {lo}..{hi})"
        )


def validate_enum(name: str, value: str, allowed: list[str]) -> None:
    if value not in allowed:
        raise InvalidEnvValue(f"{name}={value!r}: not in enum {allowed!r}")


def validate_boolean_flag(name: str, value: str) -> None:
    """Mode-flag / per-action vars accept `1` (set) or unset; refuse other values."""
    if value not in ("1", ""):
        raise InvalidEnvValue(
            f"{name}={value!r}: boolean-style flag accepts '1' or unset only"
        )


__all__ = [
    "validate_model_pin",
    "validate_range",
    "validate_enum",
    "validate_boolean_flag",
    "MODEL_PIN_RE",
    "InvalidEnvValue",
]
