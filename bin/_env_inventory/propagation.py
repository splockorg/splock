"""Propagation-class enum + class-aware read discipline (implplan §I.impl.4).

Closed enum of seven propagation classes (six per the §I.impl.4 table plus
`operator-set-debug-toggle` which appears as a distinct class for the
`OVERNIGHT_DEBUG_RETRY_PROMPT` row). Each class carries a per-class read
contract; consumers MUST honor it.

`resolve(name)` is the consumer-facing helper. For
`operator-set-runtime-tunable` vars within an active chain, callers should
prefer the manifest-precedence path; this helper returns the live-env value
and is intended for pre-chain-start reads + non-cap consumer entry points.
The cap-from-manifest contract (I.impl.5) is enforced at the chain driver
level — `_chain_overnight/manifest.py::read_cumulative_cost` and the cap
checks in `cap_enforcement.py` already read from the manifest; this module
does not re-implement that path.
"""

from __future__ import annotations

import enum
import os
import re
from typing import Any, Optional


class PropagationClass(str, enum.Enum):
    """Closed enum of propagation classes per §I.impl.4."""

    OPERATOR_SET_RUNTIME_TUNABLE = "operator-set-runtime-tunable"
    OPERATOR_SET_PER_ACTION = "operator-set-per-action"
    OPERATOR_SET_MODE_FLAG = "operator-set-mode-flag"
    DRIVER_SET_CHAIN_CONTEXT = "driver-set-chain-context"
    OPERATOR_SET_MODEL_PIN = "operator-set-model-pin"
    OPERATOR_SET_DEBUG_TOGGLE = "operator-set-debug-toggle"
    INHERITED_FROM_BROADER_SYSTEM = "inherited-from-broader-system"


# Strict model-pin regex per §I.impl.9 #3 RATIFIED 2026-05-21.
# Matches `claude-(opus|sonnet|haiku)-N-N[-YYYYMMDD]`. The date-stamp suffix
# is optional in the regex itself (haiku ships with -YYYYMMDD; opus/sonnet
# date-stamping is operator-discipline-enforced at the chain driver).
MODEL_PIN_RE: re.Pattern[str] = re.compile(
    r"^claude-(opus|sonnet|haiku)-[0-9]+-[0-9]+(-[0-9]{8})?$"
)


class InvalidEnvValue(ValueError):
    """Raised when an env-var value fails validation (range / regex / enum).

    Message format: `<NAME>=<value>: <reason>`.
    """


def resolve(name: str, *, env: Optional[dict[str, str]] = None) -> Any:
    """Read + validate an env var per its registry entry.

    Returns the typed value (cast per `spec.type`); returns `spec.default`
    when unset. Raises `InvalidEnvValue` when the value is present but
    fails range / regex / enum checks.

    `env` defaults to `os.environ`; tests inject via `monkeypatch.setenv`
    and call `resolve(name)` directly.

    NOTE: cap-from-manifest precedence (I.impl.5) is enforced at the chain
    driver level, not here. This helper returns the live-env value.
    """
    from bin._env_inventory.registry import REGISTRY  # avoid circular

    if name not in REGISTRY:
        raise KeyError(f"env var {name!r} not in REGISTRY")
    spec = REGISTRY[name]
    src = env if env is not None else os.environ
    raw = src.get(name)
    if raw is None or raw == "":
        return spec.default
    return _cast_and_validate(name, raw, spec)


def _cast_and_validate(name: str, raw: str, spec: Any) -> Any:
    """Cast raw string + validate against spec. Raises InvalidEnvValue on failure."""
    if spec.type == "string":
        if spec.regex is not None:
            if not re.match(spec.regex, raw):
                raise InvalidEnvValue(
                    f"{name}={raw!r}: does not match regex {spec.regex!r}"
                )
        if spec.valid_values is not None and raw not in spec.valid_values:
            raise InvalidEnvValue(
                f"{name}={raw!r}: not in valid_values {spec.valid_values!r}"
            )
        return raw
    if spec.type == "int":
        try:
            value = int(raw)
        except ValueError as e:
            raise InvalidEnvValue(f"{name}={raw!r}: not an integer") from e
        _check_range(name, value, spec)
        return value
    if spec.type in ("float", "numeric"):
        try:
            value = float(raw)
        except ValueError as e:
            raise InvalidEnvValue(f"{name}={raw!r}: not numeric") from e
        _check_range(name, value, spec)
        return value
    raise InvalidEnvValue(f"{name}: unknown spec.type {spec.type!r}")


def _check_range(name: str, value: float, spec: Any) -> None:
    if spec.min is not None and value < spec.min:
        raise InvalidEnvValue(
            f"{name}={value}: below min {spec.min} (valid range "
            f"{spec.min}..{spec.max if spec.max is not None else 'unbounded'})"
        )
    if spec.max is not None and value > spec.max:
        raise InvalidEnvValue(
            f"{name}={value}: above max {spec.max} (valid range "
            f"{spec.min}..{spec.max})"
        )


__all__ = [
    "PropagationClass",
    "MODEL_PIN_RE",
    "InvalidEnvValue",
    "resolve",
]
