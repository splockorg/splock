"""Schema-version lookup + forward-compat refusal.

Single source of truth for schema version dispatch. Both `bin/render_plan`
and `bin/verify_plan` route through `resolve_schema` so the refusal logic
cannot drift (per implplan §B.impl.6 lines 1219-1262).

On unsupported version: callers receive `UnsupportedSchemaVersion` and
emit exit code 5 with a structured stderr JSON envelope.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# Resolved at module import: schemas/ directory at repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMAS_DIR = _REPO_ROOT / "schemas"

SUPPORTED_VERSIONS_PLAN: tuple[int, ...] = (1,)
SUPPORTED_VERSIONS_ORCHESTRATOR: tuple[int, ...] = (1,)
SUPPORTED_VERSIONS_STATE: tuple[int, ...] = (1,)
SUPPORTED_VERSIONS_PLAN_PATCH: tuple[int, ...] = (1,)

SchemaKind = Literal["plan", "orchestrator", "state", "plan_patch"]

# Per-kind filename overrides. By convention, `plan` and `orchestrator`
# substrates use `<kind>_v<n>.schema.json`. The `state` substrate's file
# carries a leading underscore to mirror the `_state.json` system-file
# convention (sealed-state, CLI-only writes per v2.7 §5.B / §E.2).
# `plan_patch` uses the default `plan_patch_v<n>.schema.json` convention
# (the surgical-amend patch object per plan_surgical_amend SC1) — no
# override needed.
_FILENAME_KIND_OVERRIDE: dict[str, str] = {
    "state": "_state",
}


@dataclass(frozen=True)
class UnsupportedSchemaVersion(Exception):
    """Raised when `schema_version` is outside the supported set.

    Carries the structured-error payload that the caller emits to stderr
    verbatim (per implplan §B.impl.6 lines 1241-1251).
    """

    kind: SchemaKind
    seen: int
    supported: tuple[int, ...]
    reason: Literal["unsupported_schema_version", "schema_version_too_old"]

    def __str__(self) -> str:
        return (
            f"{self.reason}: kind={self.kind} seen={self.seen} "
            f"supported={list(self.supported)}"
        )

    def as_stderr_payload(self) -> dict:
        return {
            "error": self.reason,
            "kind": self.kind,
            "seen": self.seen,
            "supported": list(self.supported),
        }


def _supported_for(kind: SchemaKind) -> tuple[int, ...]:
    if kind == "plan":
        return SUPPORTED_VERSIONS_PLAN
    if kind == "orchestrator":
        return SUPPORTED_VERSIONS_ORCHESTRATOR
    if kind == "state":
        return SUPPORTED_VERSIONS_STATE
    if kind == "plan_patch":
        return SUPPORTED_VERSIONS_PLAN_PATCH
    raise ValueError(f"unknown schema kind: {kind!r}")


def is_supported_version(kind: SchemaKind, version: int) -> bool:
    return version in _supported_for(kind)


def resolve_schema(kind: SchemaKind, version: int) -> dict:
    """Return the JSON Schema dict for (`kind`, `version`).

    Raises:
        UnsupportedSchemaVersion: with `reason='unsupported_schema_version'`
            if version > max supported; `reason='schema_version_too_old'` if
            version < min supported. Both are mapped to exit code 5 by the
            caller per implplan §B.impl.4 line 1154.
    """
    supported = _supported_for(kind)
    if version > max(supported):
        raise UnsupportedSchemaVersion(
            kind=kind,
            seen=version,
            supported=supported,
            reason="unsupported_schema_version",
        )
    if version < min(supported):
        raise UnsupportedSchemaVersion(
            kind=kind,
            seen=version,
            supported=supported,
            reason="schema_version_too_old",
        )

    stem = _FILENAME_KIND_OVERRIDE.get(kind, kind)
    schema_path = _SCHEMAS_DIR / f"{stem}_v{version}.schema.json"
    with schema_path.open(encoding="utf-8") as fh:
        return json.load(fh)


def schemas_dir() -> Path:
    """Expose the canonical schemas dir; tests use this to point at fixtures."""
    return _SCHEMAS_DIR
