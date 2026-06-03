"""Env-var inventory substrate (splock implplan §I.impl).

Single source of truth for every env var any chain-related code path
consults. Consumers import name constants from `registry` and call
`propagation.resolve(name)` for class-aware read discipline.

No CLI surface; library only. Schema validation runs at import time via
`schemas/env_inventory_v1.schema.json` (per §I.impl.2 + §B.impl.6
forward-compat).
"""

from __future__ import annotations

from bin._env_inventory.registry import (
    REGISTRY,
    EnvVarSpec,
)
from bin._env_inventory.propagation import (
    PropagationClass,
    resolve,
)

__all__ = [
    "REGISTRY",
    "EnvVarSpec",
    "PropagationClass",
    "resolve",
]
